# N100 Tailscale SSH Diagnostic Handoff

This handoff is for an agent running directly on Mark's Linux N100.

## Goal

Find why the Beelink/home-admin machine cannot SSH to this N100 over Tailscale.

Expected remote source:

```text
beelink.tail65546d.ts.net
Tailscale IP: 100.79.129.106
Tag: tag:home-admin
```

Expected target:

```text
Host hostname: N100
Tailscale DNS: n100.tail65546d.ts.net
Tailscale IP: 100.88.96.43
Tag: tag:steinhorst-display
Linux user seen locally: markmontierth
```

## Known Facts From Beelink

From Beelink, the N100 is visible in the Tailscale network map:

```text
HostName: N100
DNSName: n100.tail65546d.ts.net.
TailscaleIPs: 100.88.96.43, fd7a:115c:a1e0::3732:602c
Tags: tag:steinhorst-display
Online: true
sshHostKeys: 3
PeerAPIURL: http://100.88.96.43:46333
```

Tailscale discovery ping works from Beelink:

```bash
tailscale ping 100.88.96.43
```

But actual OS/TCP paths intermittently or consistently fail from Beelink:

```text
tailscale ping --icmp 100.88.96.43       sometimes timed out
tailscale ping --peerapi 100.88.96.43    sometimes timed out
nc -vz 100.88.96.43 22                   timed out
tailscale ssh markmontierth@100.88.96.43 timed out
tailscale ssh 100.88.96.43               timed out
```

Beelink's local `tailscaled` logs show timeouts, not explicit ACL denies:

```text
open-conn-track: timeout opening (TCP 100.79.129.106:... => 100.88.96.43:22) to node [C39IY]; online=yes
```

For comparison, Beelink can reach the old/known display on TCP/22:

```text
100.66.199.1:22 succeeds
```

## Known Facts From N100

The N100 has a normal system `tailscaled`, not userspace mode:

```text
/usr/sbin/tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/run/tailscale/tailscaled.sock --port=41641
```

The N100 has a real `tailscale0` interface:

```text
tailscale0 inet 100.88.96.43/32
tailscale0 inet6 fd7a:115c:a1e0::3732:602c/128
```

Tailscale SSH is enabled:

```text
"RunSSH": true
```

OpenSSH is installed, active, and listening on all addresses:

```text
ssh.service active (running)
Server listening on 0.0.0.0 port 22.
Server listening on :: port 22.
sshd listening on 0.0.0.0:22 and [::]:22
```

UFW is inactive:

```text
Status: inactive
```

Tailscale iptables chains include:

```text
-A ts-input -i tailscale0 -j ACCEPT
```

Mark can SSH to the N100 from a computer on the LAN, so `sshd` is not globally broken.

## Important Clarification

The LAN IP, such as `192.168.123.47`, should not affect Tailscale SSH to `100.88.96.43`.

The possible old LAN address `192.168.123.43` may matter only for local LAN DNS/router records or LAN SSH, not for the Tailscale IP path being tested here.

## Policy Context

Relevant tailnet ACL/policy excerpt supplied by the operator:

```json
{
  "acls": [
    {
      "action": "accept",
      "src": ["tag:home-admin"],
      "dst": ["tag:steinhorst-display:22"]
    }
  ],
  "ssh": [
    {
      "action": "accept",
      "src": ["tag:home-admin"],
      "dst": ["tag:steinhorst-display"],
      "users": ["root", "autogroup:nonroot"]
    }
  ],
  "tagOwners": {
    "tag:home-admin": ["autogroup:admin"],
    "tag:steinhorst-display": ["autogroup:admin"]
  }
}
```

Note: `tag:all` in Tailscale policy is just a tag named `all`, not a wildcard.

## Diagnostic Plan To Run On N100

Run commands locally on the N100. Do not reboot unless the evidence points to a stuck service.

### 1. Confirm Tailnet Identity

```bash
hostname
hostname -I
tailscale ip -4
tailscale status
tailscale whois 100.79.129.106
```

Expected:

```text
tailscale ip -4 => 100.88.96.43
whois 100.79.129.106 identifies beelink/home-admin
```

If `tailscale status` does not show Beelink or `tailscale whois 100.79.129.106` fails, investigate tailnet/account mismatch.

### 2. Test Reverse OS-Level Tailscale Traffic

```bash
tailscale ping 100.79.129.106
tailscale ping --icmp 100.79.129.106
tailscale ping --peerapi 100.79.129.106
```

Record exact output. Discovery ping alone is not enough; `--icmp` and `--peerapi` test actual OS/PeerAPI paths.

### 3. Watch For Incoming Beelink SSH Attempts

Ask the Beelink operator to try SSH while this runs:

```bash
sudo tcpdump -ni tailscale0 host 100.79.129.106 and port 22
```

Interpretation:

```text
No packets:
  Packets are not reaching N100. Suspect Tailscale policy/effective ACL, tailnet mismatch, or Tailscale connectivity.

SYN packets arrive but no SYN-ACK leaves:
  Local firewall/kernel packet handling issue on N100.

SYN and SYN-ACK are visible:
  Network path works; investigate SSH authentication/Tailscale SSH user policy.
```

If `tcpdump` is missing:

```bash
sudo apt update
sudo apt install -y tcpdump
```

### 4. Check Firewall Rules Beyond UFW

Even with UFW inactive, nftables or custom iptables rules may exist.

```bash
sudo iptables -S
sudo iptables -L -n -v
sudo nft list ruleset
```

Look for drops/rejects involving:

```text
tailscale0
100.64.0.0/10
100.79.129.106
100.88.96.43
tcp dport 22
```

### 5. Check SSH Runtime Logs During A Beelink Attempt

In one terminal:

```bash
sudo journalctl -fu ssh
```

In another terminal or by coordinating with Beelink, trigger:

```bash
tailscale ssh markmontierth@100.88.96.43
```

Interpretation:

```text
No ssh log entries:
  Connection never reached sshd. Stay focused on packet path/firewall/Tailscale policy.

Preauth/auth entries:
  Connection reached sshd. Investigate user/auth rules.
```

### 6. Check Tailscale Logs During Attempt

```bash
sudo journalctl -fu tailscaled
```

Look for:

```text
acl
drop
reject
denied
ssh
100.79.129.106
100.88.96.43
```

Also run a short retrospective search:

```bash
sudo journalctl -u tailscaled --since '30 minutes ago' --no-pager | \
  grep -Ei 'acl|drop|reject|denied|ssh|100\.79\.129\.106|100\.88\.96\.43|22'
```

### 7. Validate OpenSSH Bind And Local Tailscale Address

```bash
sudo ss -ltnp | grep ':22'
ssh -vvv -o BatchMode=yes -o ConnectTimeout=8 markmontierth@100.88.96.43 hostname
```

If local SSH to `100.88.96.43` fails on the N100 itself, inspect `/etc/ssh/sshd_config` and local firewall/routing.

### 8. Optional Safe Fixes If Evidence Points To Local Filtering

If UFW is active:

```bash
sudo ufw allow in on tailscale0 to any port 22 proto tcp
```

Restart services only after collecting logs:

```bash
sudo systemctl restart ssh
sudo systemctl restart tailscaled
```

Then re-check:

```bash
tailscale ip -4
ip addr show tailscale0
sudo ss -ltnp | grep ':22'
```

## Desired Final Report

Return:

1. Output of `tailscale status` and `tailscale whois 100.79.129.106`.
2. Output of reverse pings to Beelink, especially `--icmp` and `--peerapi`.
3. `tcpdump` result while Beelink attempts SSH.
4. Any `ssh` or `tailscaled` journal entries during the attempt.
5. Whether local `ssh markmontierth@100.88.96.43 hostname` succeeds on the N100.
