# Hiện trạng ban đầu (Baseline)

Số liệu đo trên máy phát triển **trước khi áp dụng bất kỳ tối ưu nào**. Dùng làm mốc so sánh sau mỗi thay đổi.

- **Ngày đo:** 2026-10-03
- **Máy:** Windows 11 Pro 10.0.26200, máy bàn
- **Phương pháp:** ping 60 gói / mục tiêu, cách nhau 150ms; event log 7 ngày gần nhất

## Sơ đồ mạng

```
ISP ── Modem nhà mạng (bridge mode) ──cable── Huawei WiFi BE3 (PPPoE, DHCP, phát Wi‑Fi) ~~5GHz~~ PC (MediaTek MT7922)
```

*(Cập nhật 14:20: model thực tế là **华为路由 BE3 Pro 雷电版**, bản nội địa Trung Quốc, firmware 6.1.0.11, HarmonyOS 6.1.0. Thông số dưới đây là của BE3 bản quốc tế, chỉ để tham khảo.)*

Huawei WiFi BE3: Wi‑Fi 7 dual-band 2×2 (BE3600), 1 cổng 2.5G + 3 cổng 1G (WAN/LAN tự nhận), hỗ trợ HUAWEI Mesh+ và 802.11k/v/r.

**Cập nhật 2026-10-03 11:45:** người dùng có sẵn một **mesh VNPT iGate 302S** nhưng **chưa bật** (chưa rõ có tương thích với BE3 không; chưa tìm được thông số công khai của model này). PC đang kết nối **trực tiếp BE3**. BSSID `02:5e:00:9a:40:24` khác MAC LAN của BE3 (`64:A2:8A:0E:0E:9A`) chỉ vì BE3 dùng địa chỉ locally-administered cho radio 5GHz. *(Suy luận trước đó rằng PC đi qua iGate là sai.)*

⇒ Các lần "mất kết nối router" trong EXP-001 nằm ở chặng **Wi‑Fi PC ↔ BE3**.

## Phần cứng & kết nối

| Mục | Giá trị |
|---|---|
| Card Wi‑Fi | TP-Link Wi-Fi 6 PCIe Adapter — chip **MediaTek MT7922** (`PCI\VEN_14C3&DEV_7922`) |
| Driver đang dùng | MediaTek 3.6.0.1434 (2026-07-01), `oem35.inf` |
| Driver khác trong driver store | MediaTek 25.40.2.585 (2025-12-05), `oem18.inf` — ứng viên để thử rollback |
| Card mạng dây | Intel I219-V — **Disconnected** (chưa cắm dây) |
| Adapter ảo | WireGuard `wt0` (NetBird, đang chạy) · OpenVPN DCO (Surfshark, không kết nối) · Bluetooth PAN |
| SSID / băng tần | "HomeNet" · 5GHz · kênh 36 · 802.11ax · WPA2 |
| Tín hiệu | 75% · RSSI **−66 dBm** · Rx 216 / Tx 360 Mbps |
| Nhiễu kênh | 2 mạng khác cùng kênh 36 (57–60%) |
| Gateway | 192.168.3.1 |
| DNS (IPv4) | 1.1.1.1, 1.0.0.1, 8.8.8.8 (đặt tĩnh) · IPv6: DNS link-local của router |

## Chất lượng đường truyền

| Mục tiêu | Mất gói | Trung bình | Min / Max | Jitter |
|---|---|---|---|---|
| Router 192.168.3.1 | 0/60 | 5.4 ms | 3 / 36 | 2.8 ms |
| Cloudflare 1.1.1.1 | 0/60 | 55.9 ms | 52 / 87 | 3.8 ms |
| Google 8.8.8.8 | 0/60 | 92.2 ms | 40 / 300 | **37.8 ms** |

DNS (truy vấn A, ms):

| Server | facebook.com | youtube.com |
|---|---|---|
| Router 192.168.3.1 | 5 | 9 |
| 1.1.1.1 | 660 | 52 |
| 8.8.8.8 | 62 | 51 |

Một lần phân giải facebook.com qua resolver hệ thống mất ~30s (timeout), lặp lại thì bình thường.

## Lịch sử rớt mạng (7 ngày: 2026-09-26 → 2026-10-02)

**47** sự kiện ngắt Wi‑Fi (event 8003):

| Lý do | Số lần |
|---|---|
| Disconnected by the driver | **28** |
| User wants to establish a new connection | 12 |
| Disconnected by the user | 5 |
| Temporary-disconnect request | 2 |

Theo ngày: 09-26: 1 · 09-27: 3 · 09-28: 1 · 10-01: 6 · **10-02: 36**

Sự kiện liên quan (System log):

| Nguồn | ID | Số lần | Ý nghĩa |
|---|---|---|---|
| WLAN-AutoConfig | 10002 | 16 | IHV module `mtkihvx.dll` (driver MediaTek) bị dừng |
| WLAN-AutoConfig | 4003 | 9 | Phát hiện "limited connectivity", Windows tự khôi phục |
| Tcpip | 4227 | 13 | Hết/tái sử dụng port cục bộ (TIME_WAIT: 358 kết nối lúc đo) |
| e1dexpress | 27 | 14 | Cáp mạng dây ngắt (bình thường vì không cắm) |

## Cấu hình nguồn điện & driver

| Thiết lập | Giá trị |
|---|---|
| Power plan | Balanced |
| Wireless Adapter Power Saving | AC 0 (Max Performance) / DC 2 |
| PCIe Link State Power Management (ASPM) | **AC 1 (Moderate)** / DC 2 |
| Driver `Power Saving` | **Auto** |
| Wake on Magic Packet / Pattern Match | Enabled / Enabled |
| `PnPCapabilities` | 16 (Windows được phép tắt card) |
| `TcpTimedWaitDelay` | không đặt (mặc định) |

## Kết luận sơ bộ

1. Nguyên nhân chính: **driver MediaTek crash** (10002) trùng thời điểm "disconnected by the driver".
2. Yếu tố góp phần: PCIe ASPM và power saving của card đang bật; tín hiệu ở mức trung bình; kênh 36 bị chia sẻ.
3. Đường truyền ISP ổn định (0% mất gói); jitter cao tới 8.8.8.8 là do định tuyến của nhà mạng.
4. Giải pháp triệt để nhất: dùng mạng dây (cổng Intel I219-V đang trống).
