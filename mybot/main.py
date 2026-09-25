"""
UNSW Battlecode — Competitive Dragon Bot
=========================================
Systems: World Map, BFS Pathfinding, Pearl Targeting, Sonar Coordination,
         Role System (KING/FARMER/HUNTER), Smart Splitting, Combat Logic.
"""
import sys
import random
from collections import deque

import helper as unswbc
from helper import Direction, EdgeType, Position, Team, Constants, SonarEchoes

# =============================================================================
# 1. GLOBALS
# =============================================================================

ct: unswbc.Controller
game: unswbc.Game

# Persistent per-dragon memory (each dragon process has its own)
world_map: dict = {}          # (x,y) -> tile info dict
portal_map: dict = {}         # portal_id -> list of (x, y, direction)
map_width: int = 0
map_height: int = 0
my_spawn: tuple = None        # (x,y) at round 1
enemy_intel: list = []        # decoded sonar messages about enemies
team_intel: list = []         # decoded sonar messages from allies
stuck_count: int = 0
prev_pos: tuple = None
explore_dir_idx: int = 0      # rotating exploration direction

# =============================================================================
# 2. SONAR PROTOCOL
# =============================================================================
# 64-bit packed message:
#   Bits 63-60: type (4 bits, 0-15)
#   Bits 59-54: sender ID (6 bits, 0-63)
#   Bits 53-48: X position (6 bits, 0-63)
#   Bits 47-42: Y position (6 bits, 0-63)
#   Bits 41-36: data1 (6 bits) — length or target X
#   Bits 35-30: data2 (6 bits) — round low bits or target Y
#   Bits 29-0:  reserved/aux (30 bits)

MSG_HEARTBEAT     = 0
MSG_ENEMY_SPOTTED = 1
MSG_I_AM_KING     = 4

def encode_sonar(msg_type, sender_id, x, y, d1, d2):
    return (
        ((msg_type & 0xF) << 60) |
        ((sender_id & 0x3F) << 54) |
        ((x & 0x3F) << 48) |
        ((y & 0x3F) << 42) |
        ((d1 & 0x3F) << 36) |
        ((d2 & 0x3F) << 30)
    )

def decode_sonar(msg):
    return (
        (msg >> 60) & 0xF,      # type
        (msg >> 54) & 0x3F,     # sender_id
        (msg >> 48) & 0x3F,     # x
        (msg >> 42) & 0x3F,     # y
        (msg >> 36) & 0x3F,     # d1
        (msg >> 30) & 0x3F,     # d2
    )

# =============================================================================
# 3. WORLD MAP
# =============================================================================

def update_world_map():
    """Record kelp edges, portals, and pearl timings from current vision."""
    global map_width, map_height, my_spawn
    if map_width == 0:
        map_width, map_height = game.get_map_size()

    rnd = game.get_round_num()
    pos = ct.get_position()
    if my_spawn is None:
        my_spawn = (pos.x, pos.y)

    for tile in ct.get_tiles():
        if tile is None:
            continue
        tp = tile.get_position()
        k = (tp.x, tp.y)

        kelp = set()
        for d in _DIRS:
            edge = tile.get_edge(d)
            if edge.get_edge_type() == EdgeType.KELP:
                kelp.add(d)
            elif edge.get_edge_type() == EdgeType.PORTAL:
                pid = edge.get_portal_id()
                if pid >= 0:
                    if pid not in portal_map:
                        portal_map[pid] = []
                    entry = (tp.x, tp.y, d)
                    if entry not in portal_map[pid]:
                        portal_map[pid].append(entry)

        if k not in world_map:
            world_map[k] = {'spawn_count': 0, 'had_pearl': False}

        world_map[k]['kelp'] = kelp
        world_map[k]['pearl_time'] = tile.get_pearl_time()
        world_map[k]['has_pearl'] = tile.has_pearl()
        world_map[k]['round'] = rnd

        if tile.has_pearl() and not world_map[k]['had_pearl']:
            world_map[k]['spawn_count'] += 1
        world_map[k]['had_pearl'] = tile.has_pearl()

# =============================================================================
# 4. MOVEMENT SAFETY
# =============================================================================

_DIRS = Direction.get_direction_list()  # [N, E, S, W] — cached

def _check_direction(d):
    """
    Check if moving in direction d is safe.
    Returns (passable, danger_score, has_pearl, pearl_time).
    passable=False means certain death.
    Lower danger_score = safer. has_pearl/pearl_time for tie-breaking.
    """
    pos = ct.get_position()
    here_tile = ct.get_tile(pos)
    if here_tile is None:
        return False, 999, False, 999

    # Kelp check — crossing a kelp edge is instant death
    edge = here_tile.get_edge(d)
    if edge.get_edge_type() == EdgeType.KELP:
        return False, 999, False, 999

    dest = pos.add_dir(d)
    dest_tile = ct.get_tile(dest)

    if dest_tile is None:
        # Outside vision — check world map for known kelp
        dk = (dest.x, dest.y)
        if dk in world_map:
            # Check if the *reverse* direction from dest has kelp
            # (the edge from dest's perspective going opposite)
            opposite = d.get_opposite()
            if opposite in world_map[dk].get('kelp', set()):
                return False, 999, False, 999
        # Unknown territory — risky but passable
        return True, 10, False, 999

    part = dest_tile.get_dragon()
    if part is not None:
        # Something is on that tile
        if part.get_team() == ct.get_team() and part.get_id() == ct.get_id():
            # Own body — always fatal (tail counts as occupied!)
            return False, 999, False, 999
        elif part.get_team() == ct.get_team():
            # Ally body — fatal
            return False, 999, False, 999
        else:
            # Enemy
            if part.is_head():
                # Head-on-head — both die. Only do kamikaze.
                return False, 90, False, 999
            else:
                # Enemy body — we die
                return False, 80, False, 999

    # Tile is clear — score based on threats
    danger = 0
    has_pearl = dest_tile.has_pearl()
    pearl_time = dest_tile.get_pearl_time()

    # Check adjacent tiles for enemy heads that could collide
    for adj_d in _DIRS:
        adj_pos = dest.add_dir(adj_d)
        adj_tile = ct.get_tile(adj_pos)
        if adj_tile is not None:
            adj_part = adj_tile.get_dragon()
            if adj_part is not None:
                if adj_part.get_team() != ct.get_team() and adj_part.is_head():
                    danger += 15  # enemy head nearby — might collide
                elif adj_part.get_team() == ct.get_team() and adj_part.get_id() != ct.get_id():
                    danger += 3   # ally nearby — slight crowding penalty

    return True, danger, has_pearl, pearl_time


def flood_fill_area(start_pos, first_dir, max_tiles):
    """Count how many tiles are reachable if we step in first_dir."""
    w, h = map_width, map_height
    head = start_pos
    nxt = head.add_dir(first_dir)
    
    queue = deque([(nxt.x, nxt.y)])
    visited = {(head.x, head.y), (nxt.x, nxt.y)}
    count = 0
    
    while queue and count < max_tiles:
        cx, cy = queue.popleft()
        count += 1
        
        ck = (cx, cy)
        kelp_here = world_map[ck]['kelp'] if ck in world_map else set()
        
        for d in _DIRS:
            if d in kelp_here: continue
            
            dx, dy = d.get_offset()
            nx, ny = (cx + dx) % w, (cy + dy) % h
            nk = (nx, ny)
            
            if nk in world_map:
                opp = d.get_opposite()
                if opp in world_map[nk].get('kelp', set()):
                    continue
                    
            if nk in visited:
                continue
                
            n_tile = ct.get_tile(Position(nx, ny))
            if n_tile is not None and n_tile.get_dragon() is not None:
                continue
                
            visited.add(nk)
            queue.append(nk)
            
    return count

def get_safe_moves():
    """
    Return list of (direction, danger_score, has_pearl, pearl_time)
    for all passable directions, sorted safest-first.
    """
    moves = []
    my_len = ct.get_length()
    pos = ct.get_position()
    
    for d in _DIRS:
        passable, danger, has_pearl, pearl_time = _check_direction(d)
        if passable:
            area = flood_fill_area(pos, d, my_len + 3)
            if area <= my_len:
                danger += 100 # Severe penalty for dead ends
            moves.append((d, danger, has_pearl, pearl_time))
            
    # Sort: lowest danger first, then pearl (True before False), then soonest pearl
    moves.sort(key=lambda m: (m[1], not m[2], m[3] if m[3] >= 0 else 9999))
    return moves


def pick_safe_fallback(safe_moves):
    """Pick the best fallback move from safe_moves list. Returns a Direction."""
    if safe_moves:
        return safe_moves[0][0]
    # Everything is blocked — we're dead. Pick current direction.
    return ct.get_dir()

# =============================================================================
# 5. PATHFINDING (BFS with wrapping and kelp)
# =============================================================================

def bfs_toward(target_pos, avoid_bodies=True):
    """
    BFS from head toward target, respecting kelp edges.
    Returns the first Direction to take, or None if unreachable.
    Limited to ~150 nodes explored for CPU budget.
    """
    start = ct.get_position()
    if start == target_pos:
        return None

    sx, sy = start.x, start.y
    tx, ty = target_pos.x, target_pos.y
    w, h = map_width, map_height

    queue = deque()
    visited = {(sx, sy)}

    # Seed with immediate neighbors
    here_tile = ct.get_tile(start)
    if here_tile is None:
        return None

    for d in _DIRS:
        edge = here_tile.get_edge(d)
        if edge.get_edge_type() == EdgeType.KELP:
            continue
        dx, dy = d.get_offset()
        nx, ny = (sx + dx) % w, (sy + dy) % h
        if (nx, ny) in visited:
            continue
        # Check for dragon bodies in vision
        if avoid_bodies:
            n_tile = ct.get_tile(Position(nx, ny))
            if n_tile is not None and n_tile.get_dragon() is not None:
                continue
        visited.add((nx, ny))
        if nx == tx and ny == ty:
            return d
        queue.append((nx, ny, d))  # d = first direction taken

    nodes = 0
    while queue and nodes < 150:
        cx, cy, first_dir = queue.popleft()
        nodes += 1

        # Get kelp edges from world map for this tile
        ck = (cx, cy)
        kelp_here = world_map[ck]['kelp'] if ck in world_map else set()

        for d in _DIRS:
            if d in kelp_here:
                continue
            dx, dy = d.get_offset()
            nx, ny = (cx + dx) % w, (cy + dy) % h
            # Also check reverse kelp from destination side
            nk = (nx, ny)
            if nk in world_map:
                opp = d.get_opposite()
                if opp in world_map[nk].get('kelp', set()):
                    continue
            if (nx, ny) in visited:
                continue
            visited.add((nx, ny))
            if nx == tx and ny == ty:
                return first_dir
            queue.append((nx, ny, first_dir))

    return None

# =============================================================================
# 6. PEARL TARGETING
# =============================================================================

def find_pearl_target():
    """
    Find the best pearl to pursue. Returns Position or None.
    Priority: 1) pearl on ground in vision, 2) tile spawning soon (countdown ≤ 2),
              3) explore unseen areas.
    """
    pos = ct.get_position()
    px, py = pos.x, pos.y

    best_pearl = None
    best_dist = 9999
    best_soon = None
    best_soon_dist = 9999

    for tile in ct.get_tiles():
        if tile is None:
            continue
        tp = tile.get_position()
        # Wrapped Manhattan distance
        dx = min(abs(tp.x - px), map_width - abs(tp.x - px))
        dy = min(abs(tp.y - py), map_height - abs(tp.y - py))
        dist = dx + dy
        if dist == 0:
            continue  # skip our own tile

        # Check if occupied by a dragon (not worth targeting)
        if tile.get_dragon() is not None:
            continue

        if tile.has_pearl() and dist < best_dist:
            best_dist = dist
            best_pearl = tp

        pt = tile.get_pearl_time()
        if 0 < pt <= 3 and dist < best_soon_dist:
            best_soon_dist = dist
            best_soon = tp

    if best_pearl:
        return best_pearl
    if best_soon:
        return best_soon
        
    # Fallback to heatmap (highest spawn density)
    best_density = 0
    best_heat_target = None
    best_heat_dist = 9999
    
    for k, v in world_map.items():
        density = v.get('spawn_count', 0)
        if density > 0:
            tx, ty = k
            dx = min(abs(tx - px), map_width - abs(tx - px))
            dy = min(abs(ty - py), map_height - abs(ty - py))
            dist = dx + dy
            if dist == 0: continue
            
            # Prefer higher density, tiebreak with distance
            if density > best_density or (density == best_density and dist < best_heat_dist):
                best_density = density
                best_heat_dist = dist
                best_heat_target = Position(tx, ty)
                
    if best_heat_target:
        return best_heat_target
        
    return None


def get_explore_target():
    """Generate an exploration target to discover new parts of the map."""
    global explore_dir_idx
    pos = ct.get_position()
    # Spiral outward — try positions at increasing radius in rotating directions
    offsets = [(7, 0), (0, 7), (-7, 0), (0, -7), (10, 5), (-5, 10), (-10, -5), (5, -10)]
    ox, oy = offsets[explore_dir_idx % len(offsets)]
    explore_dir_idx += 1
    return Position((pos.x + ox) % map_width, (pos.y + oy) % map_height)

# =============================================================================
# 7. SONAR PROCESSING
# =============================================================================

def process_sonar():
    """Read incoming sonar messages and echoes."""
    global enemy_intel, team_intel
    enemy_intel = []
    team_intel = []

    for msg_val in ct.get_sonar_messages():
        try:
            mtype, sid, x, y, d1, d2 = decode_sonar(msg_val)
            if mtype == MSG_HEARTBEAT:
                team_intel.append({'id': sid, 'x': x, 'y': y, 'length': d1})
            elif mtype == MSG_ENEMY_SPOTTED:
                enemy_intel.append({'x': d1, 'y': d2, 'reporter_id': sid})
        except Exception:
            pass  # malformed message from enemy team or noise


def broadcast_sonar():
    """Send heartbeat and enemy positions via sonar in all 4 directions."""
    pos = ct.get_position()
    msg = encode_sonar(MSG_HEARTBEAT, ct.get_id(), pos.x, pos.y, min(ct.get_length(), 63), 0)
    for d in _DIRS:
        ct.send_sonar(d, msg)

    # Report visible enemies
    for tile in ct.get_tiles():
        if tile is None:
            continue
        part = tile.get_dragon()
        if part is not None and part.get_team() != ct.get_team() and part.is_head():
            ep = part.get_position()
            emsg = encode_sonar(MSG_ENEMY_SPOTTED, ct.get_id(), ct.get_position().x, ct.get_position().y, ep.x, ep.y)
            for d in _DIRS:
                ct.send_sonar(d, emsg)
            break  # one enemy report per turn is enough

# =============================================================================
# 8. ROLE SYSTEM
# =============================================================================

def determine_role():
    """
    KING: dragon 0 or longest dragon — plays ultra-conservatively.
    HUNTER: sonar echoes detect enemies — chase them.
    FARMER: default — collect pearls and explore.
    """
    echoes = ct.get_sonar_echoes()
    my_id = ct.get_id()
    my_len = ct.get_length()

    # King criteria: first dragon or very long
    if my_id == 0 or my_len >= 15:
        return "KING"

    # Hunter: enemy detected nearby
    if echoes.enemy_head > 0 and my_len >= 5:
        return "HUNTER"

    # Check if enemies in vision
    for tile in ct.get_tiles():
        if tile is None:
            continue
        part = tile.get_dragon()
        if part is not None and part.get_team() != ct.get_team():
            if my_len >= 5:
                return "HUNTER"
            break

    return "FARMER"

# =============================================================================
# 9. SPLITTING LOGIC
# =============================================================================

def should_split():
    """
    Decide whether to split this turn.
    Split when: length >= 6, not too many dragons, no enemies in vision,
    enough open space nearby.
    """
    my_len = ct.get_length()
    unit_count = ct.get_unit_count()
    unit_limit = game.get_unit_limit()

    if my_len < 6:
        return False
    if unit_count >= unit_limit:
        return False
    if not ct.can_split(3):
        return False

    # Don't over-split on small maps — cap total dragons relative to map area
    map_area = map_width * map_height
    max_dragons = min(unit_limit, max(4, map_area // 20))
    if unit_count >= max_dragons:
        return False

    # Don't split if enemies visible (danger)
    for tile in ct.get_tiles():
        if tile is None:
            continue
        part = tile.get_dragon()
        if part is not None and part.get_team() != ct.get_team():
            return False

    # Don't split if in a tight corridor (< 3 safe moves)
    safe = get_safe_moves()
    if len(safe) < 2:
        return False

    # Only split in early/mid game, or if very long
    rnd = game.get_round_num()
    if rnd > 350 and my_len < 12:
        return False

    return True

# =============================================================================
# 10. COMBAT LOGIC
# =============================================================================

def find_hunt_target():
    """If HUNTER, find an enemy to chase. Returns Position or None."""
    my_team = ct.get_team()
    for tile in ct.get_tiles():
        if tile is None:
            continue
        part = tile.get_dragon()
        if part is not None and part.get_team() != my_team and part.is_head():
            return part.get_position()

    # Use sonar intel
    if enemy_intel:
        ei = enemy_intel[0]
        tx, ty = ei['x'], ei['y']
        
        # Coordinated Pincer: offset based on my ID
        my_id = ct.get_id()
        offsets = [(0, 0), (2, 0), (-2, 0), (0, 2), (0, -2)]
        ox, oy = offsets[my_id % len(offsets)]
        
        return Position((tx + ox) % map_width, (ty + oy) % map_height)

    return None

# =============================================================================
# 11. EXECUTE TURN — THE ORCHESTRATOR
# =============================================================================

def execute_turn():
    global stuck_count, prev_pos, explore_dir_idx

    # Seed RNG with game state — never deterministic across games
    rnd = game.get_round_num()
    pos = ct.get_position()
    random.seed(hash((rnd, ct.get_id(), pos.x, pos.y)))

    # Update systems
    update_world_map()
    process_sonar()
    broadcast_sonar()

    role = determine_role()
    ct.set_indicator_string(f"{role} L:{ct.get_length()} U:{ct.get_unit_count()}")

    # Splitting check (action — cannot also move)
    if should_split():
        child_size = 3 if ct.get_length() >= 6 else 2
        if ct.can_split(child_size):
            ct.do_split(child_size)
            return

    # Get safe moves
    safe_moves = get_safe_moves()
    if not safe_moves:
        # Completely trapped — all safe moves are fatal.
        # As a last resort, find any direction without kelp (we'll die to a dragon but not crash)
        here_tile = ct.get_tile(pos)
        if here_tile is not None:
            for d in _DIRS:
                edge = here_tile.get_edge(d)
                if edge.get_edge_type() != EdgeType.KELP:
                    ct.make_move(d)
                    return
        # Truly no way out — every direction is kelp. Just pick one.
        ct.make_move(ct.get_dir())
        return

    # Stuck detection — if we haven't moved in 3 turns, randomize
    cur = (pos.x, pos.y)
    if cur == prev_pos:
        stuck_count += 1
    else:
        stuck_count = 0
    prev_pos = cur

    if stuck_count >= 3:
        ct.make_move(random.choice([m[0] for m in safe_moves]))
        stuck_count = 0
        return

    # Role-based targeting
    target = None

    if role == "HUNTER":
        target = find_hunt_target()

    if target is None:
        # Pearl targeting (all roles including KING farm pearls)
        target = find_pearl_target()

    if target is None:
        target = get_explore_target()

    # Try BFS pathfinding to target
    move_dir = None
    if target is not None:
        move_dir = bfs_toward(target)

    # Validate move is safe
    safe_dirs = {m[0] for m in safe_moves}
    if move_dir is not None and move_dir in safe_dirs:
        # Check if a pearl is immediately adjacent — prefer eating it
        for sm in safe_moves:
            if sm[2]:  # has_pearl
                move_dir = sm[0]
                break
        ct.make_move(move_dir)
        return

    # BFS failed or move not safe — pick best safe move
    # Prefer: pearl > forward > safest
    for sm in safe_moves:
        if sm[2]:  # has_pearl
            ct.make_move(sm[0])
            return

    # Prefer current direction (forward momentum)
    cur_dir = ct.get_dir()
    if cur_dir in safe_dirs:
        ct.make_move(cur_dir)
        return

    # Just take safest
    ct.make_move(safe_moves[0][0])

# =============================================================================
# 12. MAIN LOOP
# =============================================================================

def main():
    global ct, game
    ct, game = unswbc.init()

    while unswbc.update(ct, game):
        try:
            execute_turn()
        except Exception:
            # Emergency fallback — never crash, never hit kelp
            safe = get_safe_moves()
            if safe:
                ct.make_move(safe[0][0])
            else:
                # Find any non-kelp direction
                here_tile = ct.get_tile(ct.get_position())
                moved = False
                if here_tile is not None:
                    for d in _DIRS:
                        if here_tile.get_edge(d).get_edge_type() != EdgeType.KELP:
                            ct.make_move(d)
                            moved = True
                            break
                if not moved:
                    ct.make_move(ct.get_dir())
        unswbc.end_turn()

if __name__ == "__main__":
    main()
