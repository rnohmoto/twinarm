#!/bin/zsh
# 読み取りだけ。この Mac のネットワーク設定が元の状態かを表示する(何も変更しない)。
ok() { print "  OK   $1"; }
ng() { print "  要確認 $1"; }
chk() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else ng "$1"; fi }
print "ネットワーク確認 $(date '+%Y-%m-%d %H:%M:%S')"
chk "bridge0 がある(Thunderbolt Bridge の装置)"            'ifconfig bridge0'
chk "Thunderbolt Bridge は DHCP"                            'networksetup -getinfo "Thunderbolt Bridge" | grep -q "^DHCP Configuration"'
chk "USB LAN は DHCP"                                       'networksetup -getinfo "USB 10/100/1000 LAN" | grep -q "^DHCP Configuration"'
chk "Wi-Fi は DHCP"                                         'networksetup -getinfo "Wi-Fi" | grep -q "^DHCP Configuration"'
chk "仮想 Ethernet(feth)が無い"                            '! ifconfig -l | tr " " "\n" | grep -q "^feth"'
chk "共有用の bridge100 が無い"                             '! ifconfig bridge100'
chk "インターネット共有のデーモンが動いていない"            '! pgrep -f /usr/libexec/InternetSharing'
chk "インターネット共有の設定が空"                          '! plutil -extract NAT.Enabled raw /Library/Preferences/SystemConfiguration/com.apple.nat.plist'
chk "IPv4 転送フラグ 0"                                     '[ "$(sysctl -n net.inet.ip.forwarding)" = 0 ]'
chk "IPv6 転送フラグ 0"                                     '[ "$(sysctl -n net.inet6.ip6.forwarding)" = 0 ]'
chk "既定の経路が Wi-Fi(en0)"                               'route -n get default | grep -q "interface: en0"'
chk "Wi-Fi に手動 DNS が無い"                               'networksetup -getdnsservers Wi-Fi | grep -q "any DNS"'
chk "外部に届く(github.com)"                                'curl -s -m 6 -o /dev/null https://github.com'
print "  値: 経路 $(route -n get default 2>/dev/null | awk "/gateway/{print \$2}") / DNS $(scutil --dns | awk "/nameserver\\[0\\]/{print \$3; exit}") / Wi-Fi IP $(ipconfig getifaddr en0) / サービス順 $(networksetup -listnetworkserviceorder | grep -E "^\\([0-9]" | sed "s/^([0-9]*) //" | tr "\n" ">" | sed "s/>$//")"
