# UNSW Battlecode - Python Bot Setup

This repository contains the Python bot setup for the **UNSW Battlecode** competition.

- Quickstart Guide: [https://game.battlecode.au/docs/quickstart](https://game.battlecode.au/docs/quickstart)
- Competition Platform: [https://game.battlecode.au/](https://game.battlecode.au/)
- Map Editor: [https://game.battlecode.au/map-editor](https://game.battlecode.au/map-editor)
- Web Replay Visualiser: [https://game.battlecode.au/visualiser](https://game.battlecode.au/visualiser)

---

## 📂 Project Structure

```text
.
├── .gitignore          # Ignores replays, cache, and build files
├── README.md           # Quickstart and setup guide
├── requirements.txt    # Python dependencies (`unswbc`)
├── maps/               # Bundled and custom game maps (*.map)
│   ├── Colosseum.map
│   ├── arena.map
│   ├── big_empty.map
│   └── ...
└── mybot/              # Bot directory
    ├── .gitignore      # Ignores .unswbc-build
    ├── bot.toml        # Bot project configuration
    ├── helper.py       # Engine protocol & helper functions (provided by unswbc)
    └── main.py         # Bot logic and main turn execution loop
```

---

## 🚀 Setup & Installation

### 1. Prerequisites
- **Python 3.11+** installed.
- [uv](https://docs.astral.sh/uv/) (recommended) or `pip`.

### 2. Install the Toolkit (`unswbc`)
With **uv** (recommended):
```bash
uv tool install unswbc
```

Or using **pip**:
```bash
pip install -r requirements.txt
```

### 3. Verify Your Environment
Run `unswbc` to check your environment:
```bash
unswbc
```
This checks for Python, compilers, and the replay viewer.

---

## 🎮 Running Matches

Play a match between your bot and itself (or another bot):
```bash
unswbc run maps/arena.map mybot mybot
```

### Useful Flags
- `-v`: Verbose output (logs every round and dragon output line by line).
- `--sandbox`: Run the bot inside the official contest sandbox (enforces CPU point limits, caps logs/drawings).
- `-o <folder_or_path>`: Specify custom replay destination (default is `replays/`).

Example:
```bash
unswbc run -v maps/arena.map mybot mybot
```

---

## 📺 Viewing Replays

### Option 1: In VS Code / Cursor
If using VS Code or Cursor, install the replay extension:
```bash
unswbc vscode
```
Then simply click any `.replay` file in the `replays/` folder.

### Option 2: Web Visualiser
Upload your `.replay` file to the [UNSW Battlecode Visualiser](https://game.battlecode.au/visualiser).

---

## 🛠️ Bot Development

Edit [mybot/main.py](mybot/main.py) to implement your strategy.

The game loop structure:
```python
import helper as unswbc

ct: unswbc.Controller
game: unswbc.Game

def execute_turn() -> None:
    # Your decision-making logic per turn
    ...

def main() -> None:
    global ct, game
    ct, game = unswbc.init()

    while unswbc.update(ct, game):
        execute_turn()
        unswbc.end_turn()

if __name__ == "__main__":
    main()
```

### Updating Helpers and Maps
When the toolkit is upgraded:
- Update bot helper files: `unswbc update mybot`
- Fetch newly bundled maps: `unswbc maps`

---

## 🚀 Submitting to the Competition

1. Obtain your team API key from [https://game.battlecode.au/team](https://game.battlecode.au/team) (starts with `bc_`).
2. Authenticate locally:
   ```bash
   unswbc auth set bc_YOUR_KEY_HERE
   ```
3. Check your authentication status:
   ```bash
   unswbc auth status
   ```
4. Submit your bot:
   ```bash
   unswbc submit -n "v1.0" -d "Initial starter bot" mybot
   ```
   Or zip the `mybot/` directory (ensuring `bot.toml` is at the root of the archive) and upload directly at [Submissions](https://game.battlecode.au/submissions).
