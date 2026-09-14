#!/usr/bin/env bash
# Host INPUT traffic only; never flush the host ruleset or Docker chains.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  printf '%s\n' 'Run this host firewall setup as root.' >&2
  exit 1
fi

chain='OBSERVABILITY-NODE'
iptables -w -S "$chain" >/dev/null 2>&1 || iptables -w -N "$chain"

# Ensure the intended rules before exposing the chain through INPUT. On a
# repeated run no rules are duplicated. The listener is IPv4-only.
iptables -w -C "$chain" -i lo -j ACCEPT 2>/dev/null ||
  iptables -w -A "$chain" -i lo -j ACCEPT
iptables -w -C "$chain" -i br-observe -s 172.28.0.0/24 -j ACCEPT 2>/dev/null ||
  iptables -w -A "$chain" -i br-observe -s 172.28.0.0/24 -j ACCEPT
iptables -w -C "$chain" -j DROP 2>/dev/null ||
  iptables -w -A "$chain" -j DROP
iptables -w -C INPUT -p tcp --dport 9100 -j "$chain" 2>/dev/null ||
  iptables -w -I INPUT 1 -p tcp --dport 9100 -j "$chain"
