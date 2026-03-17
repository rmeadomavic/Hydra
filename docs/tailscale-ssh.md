# Tailscale SSH — Remote Access to Jetson

Tailscale creates a private mesh VPN between your devices. With Tailscale SSH
enabled, you can SSH into the Jetson from any machine on your Tailscale
network — no port forwarding, no public IP, no SSH key setup.

## How It Works

1. The Jetson and your laptop both run Tailscale, logged into the same account
2. Tailscale assigns each device a stable IP (100.x.x.x)
3. Tailscale SSH handles authentication — no passwords or keys needed

## Connecting from Windows

Open PowerShell or Windows Terminal:

```
ssh sorcc@<jetson-tailscale-ip>
```

Find the Jetson's Tailscale IP:
- On the Jetson: `tailscale ip -4`
- In the Tailscale admin console: https://login.tailscale.com/admin/machines

You can also use the machine name:
```
ssh sorcc@sorcc-desktop
```

## Connecting from Mac/Linux

Same command:
```
ssh sorcc@<jetson-tailscale-ip>
```

## Setting Up Tailscale (if not done during setup)

If you skipped Tailscale during `hydra-setup.sh`, you can install it manually:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale set --ssh
```

Follow the auth URL printed by `tailscale up` to link the device to your
Tailscale account.

## Troubleshooting

### "Connection refused" or timeout
- Is Tailscale running on both machines? Check: `tailscale status`
- Are both machines on the same Tailscale account?
- Try pinging the Tailscale IP: `ping <jetson-tailscale-ip>`

### "Permission denied"
- Make sure you're using the right username: `ssh sorcc@...` (not your
  Windows username)
- Is Tailscale SSH enabled on the Jetson? Check: `tailscale status`
  Look for "SSH" in the output. If missing: `sudo tailscale set --ssh`

### Tailscale not starting on boot
```bash
sudo systemctl enable tailscaled
sudo systemctl start tailscaled
```

### Need to re-authenticate
```bash
sudo tailscale up --reset
```
Follow the new auth URL.
