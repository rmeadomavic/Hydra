# tmux + Claude Code Quick Reference

> Print this. Tape it to your monitor.
> Your Jetson prefix key is **Ctrl+a** (not the default Ctrl+b).

---

## Starting a Session

| What | Command |
|------|---------|
| Create Hydra session | `tmux new-session -s hydra -c ~/Hydra` |
| Create with split panes | `tmux new -s hydra -c ~/Hydra \; split-window -h -c ~/Hydra` |
| List sessions | `tmux ls` |
| Attach to Hydra | `tmux attach -t hydra` |
| Attach (or create if missing) | `tmux attach -t hydra \|\| tmux new -s hydra -c ~/Hydra` |

---

## Inside tmux — Keybindings

All commands start with **Ctrl+a**, then the next key.

### Session / Window Management

| Keys | Action |
|------|--------|
| `Ctrl+a, d` | **Detach** (session keeps running) |
| `Ctrl+a, c` | New window (tab) |
| `Ctrl+a, ,` | Rename current window |
| `Ctrl+a, $` | Rename session |
| `Ctrl+a, w` | List all windows |
| `Ctrl+a, n` | Next window |
| `Ctrl+a, p` | Previous window |

### Pane Splitting & Navigation

| Keys | Action |
|------|--------|
| `Ctrl+a, \|` | Split vertical (side by side) |
| `Ctrl+a, -` | Split horizontal (top/bottom) |
| `Ctrl+a, h` | Move to left pane |
| `Ctrl+a, j` | Move to pane below |
| `Ctrl+a, k` | Move to pane above |
| `Ctrl+a, l` | Move to right pane |
| `Ctrl+a, x` | Kill current pane |
| `Ctrl+a, z` | Zoom pane (toggle fullscreen) |

### Scrolling & Copy Mode

| Keys | Action |
|------|--------|
| `Ctrl+a, [` | Enter scroll/copy mode |
| `Ctrl+u` | Scroll up (in copy mode) |
| `Ctrl+d` | Scroll down (in copy mode) |
| `/` | Search forward (in copy mode) |
| `q` | Exit copy mode |
| Mouse scroll | Also works (mouse is enabled) |

---

## Daily Workflow

### Morning at SORCC
```bash
# Start (or resume) the Hydra session
tmux attach -t hydra || tmux new -s hydra -c ~/Hydra

# Left pane: Claude Code
claude

# Right pane (Ctrl+a, l to switch): monitoring
docker logs -f hydra 2>&1
# or: journalctl -u hydra -f
# or: tegrastats
```

### Taking a Break / Switching Tasks
```
Ctrl+a, d          <-- detach, go fly a pack
tmux attach -t hydra   <-- come right back, full context intact
```

### From Home (Windows PC or Laptop)
```powershell
ssh sorcc@100.109.160.122 -t "tmux attach -t hydra || tmux new -s hydra -c ~/Hydra"
```

### Multiple Projects
```bash
tmux new -s rf-work -c ~/Hydra    # separate session for RF homing
tmux new -s curriculum             # separate session for class prep
tmux ls                            # see all sessions
tmux attach -t rf-work             # switch sessions
```

---

## Windows Terminal One-Click Profiles

Add these in Windows Terminal Settings > Add New Profile:

**Jetson Hydra Dev**
- Command: `ssh sorcc@100.109.160.122 -t "tmux attach -t hydra || tmux new -s hydra -c ~/Hydra"`

**Jetson Scratch**
- Command: `ssh sorcc@100.109.160.122 -t "tmux attach -t scratch || tmux new -s scratch"`

**Pi RF Monitor** *(replace IP)*
- Command: `ssh pi@<pi-tailscale-ip> -t "tmux attach -t rf || tmux new -s rf"`

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `no sessions` on attach | Session doesn't exist yet — use the `\|\|` create fallback |
| Ctrl+b does nothing | Correct — prefix is Ctrl+a on this system |
| Can't scroll | `Ctrl+a, [` enters copy mode, then scroll with mouse or Ctrl+u/d |
| Session lost after reboot | tmux sessions don't survive reboots — just recreate |
| Panes too small | `Ctrl+a, z` to zoom one pane fullscreen, toggle back with same keys |
| Claude Code not found | `source ~/.bashrc` or check `~/.local/bin` is in PATH |
