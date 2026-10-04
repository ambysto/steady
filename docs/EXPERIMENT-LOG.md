# Nhật ký thử nghiệm

Ghi lại mọi thay đổi cấu hình thực hiện **bằng tay** trên máy (trước khi có tool), kèm giá trị trước/sau, cách khôi phục và kết quả quan sát. Mỗi mục là một thử nghiệm; chỉ thay đổi một nhóm yếu tố mỗi lần để biết cái gì có tác dụng.

**Chỉ số chính để đánh giá** (so với [BASELINE.md](BASELINE.md)):

| Chỉ số | Nguồn | Baseline (7 ngày trước 2026-10-03) |
|---|---|---|
| Số lần "disconnected by the driver" / 24h | Event 8003 | 4.0 (28 lần / 7 ngày; riêng 10-02: hơn 20) |
| Số lần IHV module MediaTek dừng / 24h | Event 10002 | 2.3 (16 / 7 ngày) |
| Mất gói tới router | `ping-logger` | 0% (mẫu ngắn 60 gói) |
| Số lần mất kết nối router (≥ 3s) | `ping-logger` | chưa có số liệu |

Xem nhanh: `powershell -ExecutionPolicy Bypass -File scripts\monitor\report.ps1`

---

## EXP-001 — Tắt các cơ chế tiết kiệm điện của card Wi‑Fi và PCIe

- **Thời điểm áp dụng:** 2026-10-03 10:59:52 (+07)
- **Thực hiện bằng:** [scripts/manual/apply-lowrisk.ps1](../scripts/manual/apply-lowrisk.ps1) (quyền Admin)
- **Bản sao lưu:** `data/manual/backup-20261003-105949.json` · log: `data/manual/apply-20261003-105949.log`
- **Giả thuyết:** driver MediaTek MT7922 bị treo/crash khi card hoặc khe PCIe vào trạng thái tiết kiệm điện ⇒ tắt các cơ chế này sẽ giảm mạnh số lần "disconnected by the driver" và event 10002.

| Tweak ID | Thiết lập | Trước | Sau |
|---|---|---|---|
| `wifi_power_saving` | Driver: Power Saving | Auto | **Disabled** |
| `wifi_wake_magic` | Driver: Wake on Magic Packet | Enabled | **Disabled** |
| `wifi_wake_pattern` | Driver: Wake on Pattern Match | Enabled | **Disabled** |
| `device_power_off` | Registry `PnPCapabilities` (class key `…\0011`) | 16 | **24** |
| `power_wireless_max` | Power plan: Wireless Adapter Power Saving (AC / DC) | 0 / 2 | **0 / 0** |
| `power_pcie_aspm_off` | Power plan: PCIe Link State Power Management (AC / DC) | **1 (Moderate)** / 2 | **0 / 0** (Off) |

Power plan: Balanced (`381b4222-…`). Thay đổi power plan chỉ có hiệu lực với plan này.

**Không áp dụng lần này** (để tách biệt tác động): `wifi_bw20_5g`, `wifi_mode_ac`, `ipv6_off` (experimental), `tcp_timedwait` (medium, cần reboot), đổi kênh router, rollback driver.

**Kết quả ngay sau khi áp dụng** (11:00, 60 gói / mục tiêu):

| Mục tiêu | Mất gói | Avg | Max | Jitter |
|---|---|---|---|---|
| Router | 0/60 | 5.0 ms | 48 | 2.0 ms |
| 1.1.1.1 | 0/60 | 68.2 ms | 231 | 11.7 ms |
| 8.8.8.8 | 0/60 | 44.0 ms | 98 | 4.2 ms |

Wi‑Fi kết nối lại sau ~9s khởi động lại card; tín hiệu 72% / −65 dBm, kênh 36, Rx 204 / Tx 360 Mbps. Kết quả tức thời không khác baseline — đúng dự kiến, vì vấn đề là rớt **ngắt quãng**; cần theo dõi nhiều ngày.

**Theo dõi:** `ping-logger` chạy nền từ 2026-10-03 ~11:02 (không có quyền Admin, dừng khi đăng xuất/khởi động lại máy).

> **Từ 2026-10-04 10:05:** logger PowerShell đã dừng và gỡ khỏi Startup; thay bằng monitor Python (`python -m app.monitor`, chạy qua Task Scheduler "StableInternet Monitor", từ 09:59). Số liệu mới nằm trong `data/metrics.db` (SQLite), không còn ở `data/monitor/*.csv`. CSV cũ giữ nguyên và nhập được bằng `Storage.import_csv_dir()`. `report.ps1` chỉ đọc CSV nên không còn thấy số liệu mới — dùng `python -m app.diagnostics` hoặc truy vấn `metrics.db`.

**Mốc đánh giá:**
- [ ] 2026-10-04 (24h) — chạy `report.ps1`, ghi kết quả bên dưới
- [ ] 2026-10-06 (72h) — kết luận: giữ / khôi phục / chuyển sang EXP-002

**Khôi phục:** chạy với quyền Admin
```powershell
powershell -ExecutionPolicy Bypass -File scripts\manual\restore.ps1 -Backup data\manual\backup-20261003-105949.json
```

> ⚠️ **Kiểm tra lại 2026-10-04 ~09:55 (chỉ đọc):** phần lớn EXP-001 **không còn áp dụng**. Chỉ `Power Saving` còn `Disabled`; `Wake on Magic Packet` / `Wake on Pattern Match` = `Enabled`, `PnPCapabilities` = 16, Wireless Adapter AC 0 / **DC 2**, PCIe ASPM **AC 1 / DC 2** — đúng các giá trị *trước* khi áp dụng. Xác nhận bằng hai cách độc lập (`apply-lowrisk.ps1 -ShowState` và khung tweak mới); card vẫn ở khóa class `0011` nên không phải driver bị cài lại. `data/manual/` không có log khôi phục nào ⇒ **không rõ bị hoàn tác lúc nào và do đâu**. Hệ quả: các kết quả tốt sau EXP-014 (xoay thùng máy) đạt được khi phần lớn EXP-001 đã không còn — càng củng cố kết luận nguyên nhân chính là ăng-ten bị che.

**Quan sát:**
- **10:59:51** — Windows ghi 1 event 8003 + 1 event 10002 + event `mtkwlex` 1033/8001/8000: đây là do **chủ động** khởi động lại card khi áp dụng, *không tính* là sự cố. Lưu ý khi đọc `report.ps1` (mốc mặc định 10:59:49 nên sẽ đếm cả 2 event này).
- **11:03:59 → 11:04:56 — mất kết nối tới router 57 giây** (logger: router mất 37/42 gói trong phút 11:04) nhưng **Wi‑Fi vẫn báo `connected`**, cùng BSSID, không có event 8003/10002/4003 nào trong Windows. ⇒ **"treo im lặng"**: liên kết vẫn giữ nhưng đường dữ liệu đứng. Windows không tự phát hiện/khôi phục ⇒ củng cố nhu cầu watchdog dựa trên ping router (đúng kịch bản `router_unreachable` trong [WATCHDOG.md](WATCHDOG.md)).
  - Ngay trước đó tốc độ Rx tụt mạnh: 6 Mbps (11:03), 34 Mbps (11:04), rồi hồi lại 122 → 360 Mbps. ⇒ Rx rate tụt đột ngột có thể là **tín hiệu cảnh báo sớm** — cân nhắc đưa vào chẩn đoán / watchdog.
  - Xảy ra chỉ ~4 phút sau khi áp dụng ⇒ EXP-001 **chưa** loại bỏ được lỗi treo; cần thêm dữ liệu để biết tần suất có giảm không.
- **Báo cáo 15 phút đầu (10:59 → 11:18):** router mất **10%** gói (82/817), 7/15 phút có mất gói; **4 lần mất kết nối router**: 57s (11:04), 25s (11:11), 3s (11:15), 27s (11:17). Wi‑Fi luôn `connected`, không roaming, không có event Windows nào.
- **Quy luật:** mọi lần mất kết nối đều trùng phút mà **Rx rate sụp về 6 Mbps** (mức MCS thấp nhất) — 11:03, 11:10, 11:17 — trong khi **tín hiệu vẫn 71–75% / −67 dBm**. Tín hiệu đủ mạnh mà tốc độ sụp ⇒ nghiêng về **nhiễu / xung đột trên kênh 36** (2 mạng hàng xóm cùng kênh), hoặc lỗi phía AP (BSSID `02:79:…` là địa chỉ locally-administered — có thể là node mesh/AP ảo), hơn là do tiết kiệm điện.
- ⚠️ **Hạn chế phương pháp:** logger chỉ bắt đầu chạy *sau* khi áp dụng EXP-001, nên **không có số liệu ping cùng phương pháp cho giai đoạn trước** ⇒ chưa thể nói EXP-001 làm tốt hơn hay tệ hơn. Bài học: luôn chạy logger trước khi thay đổi để có baseline cùng thước đo.
- 11:25 — logger **tự chạy khi đăng nhập**: shortcut trong Startup folder → `scripts/monitor/start-logger-hidden.vbs` (cài/gỡ: `scripts/monitor/install-autostart.ps1 [-Remove]`).
- 11:21–11:30 — logger PID 27080 bị dừng ngoài ý muốn (không có `logger_stop` ⇒ bị kill từ bên ngoài, nghi do process con của phiên Claude bị dọn) ⇒ **mất số liệu 11:21–11:24 và 11:26–11:29**. Đã khởi chạy lại tách khỏi phiên qua WMI (PID 28344, 11:30).
- 11:20 — sửa lỗi phân loại `internet_down` trong logger (trước đó có thể ghi nhầm "router reachable" khi router cũng mất trong cùng sự cố); khởi động lại logger.
- **11:05 → 11:08** — router 0% mất gói, nhưng 1.1.1.1 và 8.8.8.8 mất **3–17%** mỗi phút (2–10 / 60 gói) dù độ trễ bình thường. Khác với mẫu đo 60 gói lúc 11:00 (0%). Giả thuyết cần kiểm chứng: mất gói phía WAN của router/ISP, hoặc router/ISP giới hạn ICMP. ⇒ Logger nên ghi theo giây (hoặc thêm kiểm tra TCP/DNS) để phân biệt mất gói thật với ICMP bị giới hạn.

---

### Diễn biến EXP-001: 11:30 → 13:14 (trước khi đổi kênh)

`report.ps1 -Since '2026-10-03 11:30'` (107 phút):

| Mục tiêu | Mất gói | Avg | Jitter | Phút có mất gói |
|---|---|---|---|---|
| Router | **34.9%** (1920/5503) | 25.7 ms | 31.7 ms | **101/107** |
| 1.1.1.1 | 43.3% | 80.6 ms | 36.6 ms | 107/107 |
| 8.8.8.8 | 42.3% | 72.9 ms | 41.7 ms | 107/107 |

- **64 lần mất kết nối router**, tổng **2519s (~42 phút / 107 phút)**. Windows chỉ ghi 1 lần "disconnected by the driver" ⇒ gần như toàn bộ là **treo im lặng**.
- Rx rate sụp về **6 Mbps** trong hầu hết các phút (8–14 phút mỗi khung 15 phút).
- Tín hiệu giảm dần: 71% / −69 dBm (11:30–12:30) → 63% / −73 (12:45) → 57% / −76 (13:00). Nguyên nhân chưa rõ (cần hỏi: có thay đổi gì ở router/vị trí lúc ~12:45?).
- ⚠️ Tình trạng **tệ hơn rõ rệt** so với 15 phút đầu (10% mất gói). Chưa loại trừ khả năng EXP-001 (tắt power saving trên MediaTek) làm xấu đi ⇒ A/B (EXP-001b) càng cần thiết.

## EXP-002 — Đổi kênh 5GHz của BE3: 36 → 157

- **Thời điểm:** ~13:14–13:16 (+07) — người dùng đổi trên trang quản trị BE3 (tắt 自动优化信道, đặt 组网信道模式, chọn kênh thủ công). Logger thấy PC tạm roam sang 2.4G ch1 (13:14:33), sang 5G ch44 (13:15:57), cuối cùng ch157.
- **Giữ nguyên:** EXP-001 vẫn đang áp dụng; băng thông 5G 20/40/80 MHz.
- **Giả thuyết:** rời kênh 36 (chung với 2 mạng hàng xóm) sẽ hết hiện tượng Rx sụp về 6 Mbps và mất kết nối.
- **Ngay sau khi đổi (13:17):** kênh 157, **tín hiệu 48% / −76 dBm** (yếu), Rx 367 / Tx 432 Mbps. Kênh 157 không có mạng hàng xóm nào; kênh 36 còn 2 mạng (c4/c6:2c:7b…), kênh 48 có 2 mạng.
- **Lưu ý:** kênh cao (UNII‑3) suy hao qua tường nhiều hơn một chút; nếu RSSI ổn định dưới −72 dBm, cân nhắc kênh 149 hoặc chế độ công suất 穿墙 (xuyên tường).
- **Mốc đánh giá:** 13:45 và 15:00 — so sánh mất gói tới router, số lần mất kết nối, số phút Rx ≤ 6 Mbps với khung 11:30–13:14 ở trên.

**Quan sát:**
- **13:17 → 13:33 (16 phút):** router mất **19.7%** gói (12/16 phút có mất gói), 6 lần mất kết nối router (235s); Internet mất ~65%. Lần đầu xuất hiện **ngắt kết nối thật** (Wi‑Fi `disconnected` 13:29:41, 13:31:51; 5 event 8003, 1 event 10002, 1 event 4003). RSSI **−75 → −79 dBm**; Rx vẫn sụp 6 Mbps lúc 13:20–13:21. Có 1 phút PC ở kênh 36 (13:22).
- Đánh giá sơ bộ: rời kênh 36 **chưa** giải quyết được; vấn đề nổi bật hiện tại là **tín hiệu yếu** (−76…−79), vốn đã giảm từ ~12:45 khi còn ở kênh 36 ⇒ không do đổi kênh. Cần hỏi người dùng có đang thao tác trên router lúc 13:29–13:33 không.

## Phát hiện: modem nhà mạng vẫn phát Wi‑Fi dù đã bridge (13:40)

- Người dùng: modem nhà mạng đã chuyển **bridge**, "OldModemNet" là mạng cũ; **modem và BE3 đặt sát cạnh nhau**.
- Các SSID OldModemNet / CNBN / NeighborNet / SSID ẩn cùng họ MAC `c4/c6:2c:7b:…` ⇒ nhiều khả năng **đều do modem cũ phát** (bridge chỉ tắt chức năng định tuyến, không tắt radio Wi‑Fi). Chúng chiếm **5G kênh 36** và **2.4G kênh 4**.
- Hai thiết bị phát đặt sát nhau ⇒ (a) tranh chấp kênh khi cùng kênh; (b) **chặn/giảm độ nhạy máy thu** của BE3 ngay cả khi khác kênh, do sóng của modem quá mạnh ở cự ly vài cm.
- Đề xuất: tắt Wi‑Fi trên modem (nút WLAN / trang quản trị qua SSID OldModemNet / nhờ tổng đài) + đặt BE3 cách modem ≥ 1–2 m, chỗ cao, thoáng, hướng về PC. ⇒ **EXP-008**.

## EXP-008 — Tắt Wi‑Fi modem cũ + dời BE3 cách modem > 1 m

- **Thời điểm:** trước 13:54 (+07), người dùng thực hiện; ăng-ten **chưa** nghiêng.
- **Quét lúc 13:54:** OldModemNet, CNBN và SSID ẩn `c6:2c:7b:d8:ea:99` **đã biến mất** ⇒ xác nhận chúng do modem phát. **NeighborNet** (`c4:2c:7b:d4:ea:9a`, kênh 36, 63%) **vẫn còn** ⇒ hoặc radio 5G của modem chưa tắt hết, hoặc NeighborNet là thiết bị khác.
- **Xuất hiện SSID mới của BE3:** `HomeNet_5G` (02:5e:00:9a:40:24, ch157), `HomeNet_Wi-Fi5` (…:40:31, ch11), `HomeNet_5G_Wi-Fi5` (…:40:32, ch157) ⇒ người dùng đã tách băng tần và/hoặc bật mạng tương thích Wi‑Fi 5 trên BE3 (cần xác nhận). PC hiện ở `HomeNet_5G`.
- **Tín hiệu xấu hơn sau khi dời:** **−78 … −80 dBm (39–45%)**, so với −76 trước khi dời và −66 sáng nay. Rx vẫn 6 Mbps ở 8/10 phút (13:40–13:50).
- **Sự cố:** router_down 154s (13:49), 152s (13:51), và 3 lần 5s (13:52–13:54); PC roam qua 2.4G ch11 rồi về 5G.
- Logger khởi động lại lúc 13:45:50 (PID 19984) không có `logger_stop` trước đó ⇒ mất số liệu 13:43–13:44.
- **Đánh giá:** dọn nhiễu của modem đã xong, nhưng **vị trí mới làm tín hiệu tới PC yếu đi** ⇒ hiện tại tín hiệu yếu (≤ −78 dBm) là yếu tố chi phối. Cần đặt lại BE3 ở vị trí nhìn thẳng/ít tường tới PC (vẫn cách modem ≥ 1 m, nối dài dây WAN nếu cần), chỉnh ăng-ten, và/hoặc bật chế độ 穿墙.

## EXP-009 — Tắt MLO trên BE3 (người dùng thực hiện)

- **Thời điểm:** khoảng 13:52–14:01 (+07), cần người dùng xác nhận giờ chính xác.
- **Người dùng nhận xét:** "tắt MLO thì kết nối không còn rớt như trước".
- **Người dùng xác nhận thứ tự:** (1) tắt MLO → (2) đổi kênh 5G sang **48** → (3) bật chế độ **Wi‑Fi 5** (mạng tương thích) trên router.
- **Các thay đổi đi kèm, quan sát được trong cùng khoảng thời gian** (⇒ **không tách riêng được tác động của MLO**):
  - BE3 tách SSID theo băng tần: `HomeNet_5G` và các SSID tương thích Wi‑Fi 5 `HomeNet_Wi-Fi5` (2.4G ch11), `HomeNet_5G_Wi-Fi5`.
  - PC đang kết nối `HomeNet_5G_Wi-Fi5` ⇒ **chuẩn 802.11ac** (không còn ax/be) — tương đương thử nghiệm "ép Wi‑Fi 5" phía router.
  - Kênh 5G đổi từ **157 → 48** (14:02).
  - Tín hiệu tốt lên: −78/−80 dBm → **−73/−74 dBm (60%)** — có thể do kênh thấp hơn hoặc router/ăng-ten được chỉnh tiếp.
- **Số liệu theo phút (router):** 13:57–14:01 vẫn mất 8–30 gói/phút trong lúc chuyển SSID; **14:02: 0/60 mất gói, avg 6 ms, max 32 ms**, Rx 234 Mbps.
- **Kết quả 14:02 → 14:21 (19 phút):** router mất **3.1%** (35/1124) so với **34.9%** khung 11:30–13:14 và 19.7% trên kênh 157; chỉ **1 lần** mất kết nối router (3s); **0** event Windows (8003/10002/4003). RSSI −72…−76. Rx hiển thị 6 Mbps ở nhiều phút nhưng không đi kèm mất gói ⇒ chỉ số Rx rate của netsh (tốc độ khung gần nhất) không đáng tin khi lưu lượng thấp — **không dùng làm chỉ báo sự cố một mình**.
- Internet (1.1.1.1 / 8.8.8.8) vẫn mất 13–15% trong khi router chỉ 3% ⇒ vấn đề riêng phía WAN/ISP hoặc ICMP bị giới hạn — theo dõi tiếp, tách khỏi vấn đề Wi‑Fi.
- **Thông tin router (trang 路由器信息):** thực tế là **华为路由 BE3 Pro 雷电版** (BE3 Pro, bản "Thunder"), phần mềm 6.1.0.11 (V6R1), HarmonyOS 6.1.0, phần cứng VER.A; thời gian chạy 40 phút lúc ~14:20 ⇒ **router đã khởi động lại ~13:40** khi áp dụng thay đổi. WAN IP 203.0.113.10, DNS ISP 123.23.23.23 / 123.26.26.26 (VNPT). Wi‑Fi 定时 (hẹn giờ tắt Wi‑Fi): trống. Wi‑Fi 中继 (repeater): tắt. Trang kênh ghi chú: kênh dưới 149 là kênh mới mở tại TQ, một số thiết bị cũ có thể không kết nối được.
- **Đánh giá sơ bộ (14:04):** hướng đúng, khớp giả thuyết "tương thích Wi‑Fi 7 ↔ MediaTek MT7922" (MLO là tính năng Wi‑Fi 7 mà card không hỗ trợ). Nhưng mới có ~2 phút dữ liệu sạch ⇒ **chưa kết luận**. Mốc đánh giá: 15:00 và cuối ngày.

### Diễn biến EXP-009 tiếp theo: 14:21 → 14:41 (vẫn 5G ch48, SSID `HomeNet_5G_Wi-Fi5`, 802.11ac)

- Router mất **0–7 gói/phút** (~5%), mất kết nối ngắn 3–4s khoảng 5 phút/lần, không có sự cố dài. RSSI cải thiện dần −76 → −70 dBm. ⇒ **Khung tốt nhất trong ngày**, nhưng chưa hoàn hảo.

## EXP-010 — Chuyển PC sang 2.4GHz ("đổi tốc độ lấy ổn định")

- **Thời điểm:** 14:42 PC sang `HomeNet_Wi-Fi5` (2.4G, ch11); khoảng 15:20 sang `HomeNet` (2.4G ch11, 802.11ax). Logger khởi động lại 15:20:32 (nghi PC khởi động lại/đăng nhập lại).
- **Kết quả: tệ hơn rõ rệt so với 5G ch48:**
  - 14:42–14:51: 1–10 gói/phút (tương đương 5G).
  - **14:52–15:17:** 9–36 gói/phút (**~30%**), ping trung bình 70–180 ms, Rx/Tx tụt về **1 Mbps** (mức thấp nhất của 2.4G), RSSI có lúc −90…−93.
  - **15:27–15:37: mất hoàn toàn ~10.7 phút** (router_down 641s) trong khi Wi‑Fi vẫn `connected` ⇒ treo im lặng kéo dài; 15:39–15:41 lại mất 150s.
- **Nguyên nhân khả dĩ:** 2.4G kênh 11 đông (nhiều mạng hàng xóm ở kênh 4/7/11, utilization 35%), băng 2.4G dễ bị nhiễu (lò vi sóng, Bluetooth, thiết bị không dây); driver MediaTek vẫn treo.
- **Kết luận:** 2.4G **không** ổn định hơn ⇒ quay lại 5G ch48 (`HomeNet_5G_Wi-Fi5`), cấu hình tốt nhất đã đo được.
- **Nhận định tổng thể sau EXP-001 → EXP-010:** các thay đổi phía router (tắt Wi‑Fi modem, tắt MLO, kênh 48, chế độ Wi‑Fi 5) giảm mất gói từ ~35% xuống ~3–5%, nhưng **vẫn còn treo ngắn định kỳ** ở mọi băng/kênh ⇒ phần còn lại nhiều khả năng do **card/driver MediaTek + tín hiệu chỉ ở mức −70…−76 dBm**. Bước tiếp theo có giá trị nhất: **EXP-005 (cắm dây)** hoặc **đổi card Intel**.

## EXP-013 — Tắt Wi‑Fi 7 trên router, PC về `HomeNet_5G` (Wi‑Fi 6, kênh 40)

- **Thời điểm:** ~15:43 (+07). Người dùng: "đã tắt Wi‑Fi 7 và đổi về 5G". PC: `HomeNet_5G`, **802.11ax**, **kênh 40**, RSSI **−67…−68 dBm** (tốt hơn −72…−76 của ch48).
- **Kết quả 15:43 → 16:22 (39 phút):** router mất **12.7%**, **31 lần** mất kết nối (tổng 249s), 29/39 phút có mất gói; Windows chỉ 1 disconnect. Sự cố dồn nhiều quanh 16:08–16:12; từ 16:13 → 16:22 không có sự cố.
- **So sánh:** tín hiệu tốt hơn nhưng mất gói **cao hơn** khung `HomeNet_5G_Wi-Fi5` ch48 (802.11ac, ~3–5%). ⇒ Gợi ý: card MediaTek ổn định hơn ở **chế độ Wi‑Fi 5 (ac)** so với Wi‑Fi 6 (ax) — khớp hướng EXP-004 (`wifi_mode_ac`). Chưa chắc chắn vì kênh cũng đổi (48 → 40) và thời gian quan sát ngắn.
- **Mốc tín hiệu trước khi lắp ăng-ten cửa sổ:** −67 dBm.

## EXP-004 — Ép card chạy Wi‑Fi 5 (`wifi_mode_ac`)

- **Thời điểm:** ~16:26 (+07), người dùng đồng ý; chạy [scripts/manual/set-phymode.ps1](../scripts/manual/set-phymode.ps1) `-Mode ac` (Admin). Log: `data/manual/phymode-*.log`.
- **Thay đổi:** driver `802.11ax/ac/n/abg`: **1. 802.11ax → 2. 802.11ac**. Card tự khởi động lại, kết nối lại ngay.
- **Giữ nguyên (chỉ đổi một yếu tố so với EXP-013):** SSID `HomeNet_5G`, kênh 40, router ở chế độ đã tắt Wi‑Fi 7 / MLO, EXP-001 vẫn áp dụng.
- **Ngay sau khi đổi:** Radio type **802.11ac**, RSSI −69 dBm.
- **So sánh với:** EXP-013 (cùng SSID, cùng kênh, 802.11ax): router mất 12.7%, 31 lần mất kết nối / 39 phút.
- **Khôi phục:** `scripts\manual\set-phymode.ps1 -Mode ax` (Admin).

**Quan sát:**
- **16:27 → 17:26 (59 phút):** router mất **17.7%** (594/3352), **57 lần** mất kết nối (593s), 42/59 phút có mất gói; Windows 0 disconnect (toàn treo im lặng). RSSI tụt về **−73 dBm (48%)** lúc 17:26 (từ −67…−69).
- **Kết luận:** ép 802.11ac **không cải thiện**, thậm chí tệ hơn EXP-013 (12.7%) — dù tín hiệu cũng yếu đi nên không hoàn toàn công bằng. Giả thuyết "card ổn định hơn ở Wi‑Fi 5" **không được xác nhận**; khung tốt lúc 14:02–14:41 nhiều khả năng do điều kiện lúc đó (tín hiệu / nhiễu theo giờ), không phải do chuẩn Wi‑Fi.
- **17:29:50 — đã khôi phục** về `1. 802.11ax` (`set-phymode.ps1 -Mode ax`, người dùng đồng ý). PC kết nối lại `HomeNet_5G` ch40, 802.11ax, −72 dBm.
- **Nhận định:** sau 10+ thay đổi cấu hình trong ngày, mất gói dao động 3–35% theo giờ bất kể cấu hình ⇒ **chỉnh cấu hình đã hết hiệu quả**; nút thắt là **đường truyền vật lý** (xuyên sàn + mặt bàn, ăng-ten sau thùng máy) và/hoặc card MediaTek ⇒ EXP-011 (ăng-ten cửa sổ) / EXP-012 (AX3 + cable) là bước đúng.

## EXP-014 — Xoay thùng máy để ăng-ten card hướng ra ngoài

- **Thời điểm:** trong khoảng 17:32 → 18:44 (+07). Logger không có số liệu khoảng này (nghi PC tắt/khởi động lại khi xoay case; logger tự chạy lại, PID 12676). Ăng-ten nối dài **chưa** dùng.
- **Trước:** ăng-ten ở mặt sau thùng máy, quay vào trong gầm bàn, sát khung bàn kim loại và bó dây cáp (ảnh người dùng gửi).
- **Giữ nguyên:** `HomeNet_5G`, kênh 40, 802.11ax (đã khôi phục 17:29), router: Wi‑Fi modem tắt, MLO tắt, Wi‑Fi 7 tắt.
- **Kết quả 18:44 → 19:08 (25 phút):**

  | Chỉ số | 17:20–17:31 (trước) | **18:44–19:08 (sau)** |
  |---|---|---|
  | Router mất gói | 2–32 gói/phút, ~25% | **3 / 1475 gói (~0.2%)** |
  | Ping router trung bình | 5–200 ms | **3–5 ms** |
  | Rx rate | thường 6–27 Mbps | **459–600 Mbps ổn định** |
  | RSSI | −71…−76 dBm | −67…−74 dBm |

- **Nhận định:** RSSI chỉ tốt lên chút ít nhưng Rx ổn định ở mức cao và gần như hết mất gói ⇒ trước đây **một hoặc cả hai ăng-ten (2×2 MIMO) bị khung bàn kim loại / thùng máy che**, gây sụp tốc độ và treo. Đây là cải thiện lớn nhất trong ngày. Cần thêm thời gian (qua đêm, giờ cao điểm) để xác nhận.
- Ăng-ten nối dài (EXP-011) và AX3 (EXP-012) chuyển thành **có điều kiện**: chỉ cần nếu sự cố quay lại.

### Kiểm tra lúc 23:07 (sau EXP-014)

- **Số liệu 18:44 → 20:40:** router mất **0.81%** (55/6793), tổng mất kết nối 14s. RSSI 5G ch40: −63…−67 dBm, tốc độ 1201 Mbps.
- **Lỗ hổng dữ liệu 20:41 → 23:07 (2.5 giờ):** logger không chạy. Windows ghi nhiều lần OS stop/start (Kernel-General 12/13) lúc 19:48, 20:57, 20:58, 21:28 và sleep/resume lúc 17:32, 21:32 — nghi PC khởi động lại/ngủ nhiều lần (nguyên nhân chưa rõ; có thể do thao tác của người dùng quanh thùng máy).
- **Lối tắt Startup `StableInternet ping-logger.lnk` đã biến mất** (thư mục chỉ còn `Hermes_Gateway.vbs`) — không rõ ai xóa ⇒ logger không tự chạy lại sau khi PC khởi động. Đã **cài lại lúc 23:08** và khởi động logger (PID 22028).
- **Thiếu sót của logger:** kiểm tra "đã chạy" bằng `logger.pid` có thể nhầm khi PID cũ bị tái sử dụng sau khi khởi động lại ⇒ cần sửa (so khớp tên/command line của tiến trình). Ghi vào việc cần làm khi viết tool thật.
- 23:07: PC trên `HomeNet` 2.4G ch11 (−69 dBm) → người dùng chủ động chuyển về `HomeNet_5G` ch40 (−64 dBm, 802.11ax, 1201 Mbps).

### Kết quả qua đêm EXP-014: 2026-10-03 23:08 → 2026-10-04 08:41 (9.6 giờ, 573 phút)

| Mục tiêu | Mất gói | Avg | Jitter |
|---|---|---|---|
| Router | **0.39%** (133/33972) | 5.0 ms | 3.6 ms |
| 1.1.1.1 | 8.08% | 44.5 ms | 3.8 ms |
| 8.8.8.8 | 9.16% | 51.6 ms | 3.6 ms |

- **Chỉ 1 lần mất kết nối router (3s)**; Windows: 0 disconnect, 0 event IHV 10002, 0 limited connectivity. So với baseline 7 ngày trước: ~5.4 disconnect/24h và 4.1 IHV crash/24h.
- **Phần Wi‑Fi đã ổn định.** Mất gói ~8–9% ra Internet nhưng chỉ 0.39% tới router, đồng đều cả đêm (572/573 phút) và jitter thấp ⇒ nghi **ICMP bị giới hạn/ưu tiên thấp phía ISP hoặc WAN của router**, không phải Wi‑Fi. Cần kiểm tra bằng TCP/DNS (xem mục cải tiến logger).
- ⚠️ **Cập nhật 2026-10-04 (SIC-36): nghi vấn trên chưa được ủng hộ.** Monitor Python (ping song song, mỗi mục tiêu một luồng) đo mất gói ra Internet chỉ **0,3%** trong 35 phút đầu, và ở lần đo song song 09:12 logger PowerShell báo mất 8/59 trên 1.1.1.1 trong khi Python đo 0/40 cùng lúc. Con số 8–9% có thể là **sai số đo của logger PowerShell** (ping tuần tự, trễ khi gọi `netsh`) chứ không phải nhà mạng hạn chế ICMP. Chưa kết luận: mới có số liệu ban ngày. Cách phân định: so `cloudflare`/`google` (ping) với `tcp_*`/`http_*` (probe) trong `metrics.db` sau một đêm; chẩn đoán #5 đã phân biệt hai trường hợp này.
- ✅ **Phân định xong 2026-10-04 15:11 (SIC-36): mất gói ICMP là thật, nhưng chỉ ICMP bị mất, kết nối thật vẫn ổn.** Số liệu `metrics.db` 10:40 → 15:11 (4,5 giờ, 265 phút, monitor Python):

  | Mục tiêu | Gửi | Mất | Tỉ lệ |
  |---|---|---|---|
  | ping `cloudflare` 1.1.1.1 | 15 426 | 879 | **5,70%** |
  | ping `google` 8.8.8.8 | 15 426 | 1 183 | **7,67%** |
  | ping `router` | 15 426 | 123 | 0,80% |
  | `tcp_cloudflare` 1.1.1.1:443 | 1 549 | 1 | 0,06% |
  | `tcp_google` 8.8.8.8:443 | 1 549 | 2 | 0,13% |
  | `http_cloudflare` generate_204 | 1 549 | 1 | 0,06% |

  - 91/265 phút có mất ICMP, chỉ 2 phút có probe lỗi; **0 phút** mà toàn bộ ping ra Internet mất trọn và **0** sự kiện `internet_down` sinh ra từ việc chỉ ICMP mất. Giờ tệ nhất (13h) ICMP mất 19% trong khi TCP mất 0,28%.
  - Mất theo **đợt** (một phút có thể mất tới 27/58 gói), 1.1.1.1 và 8.8.8.8 mất **cùng phút** (86/89 phút), 53 trong số đó router cũng mất vài gói ⇒ không phải từng nhà cung cấp DNS giới hạn riêng; điểm nghẽn ICMP nằm chung trên đường đi (router/WAN/ISP), chưa xác định chính xác. Không ảnh hưởng tới TCP/HTTP nên không phải sự cố người dùng cảm nhận được.
  - Vậy nhận định "sai số logger PowerShell" ở trên **sai**: monitor Python cũng đo được 5–8%. Con số 0,3% lúc đầu là do đợt mất chưa xảy ra trong 35 phút đó.
  - Ghi chú phụ: thời gian bắt tay TCP tới 1.1.1.1:443 (trung vị 105 ms) cao gấp đôi ping (47 ms), còn 8.8.8.8 thì gần bằng (66 so với 54 ms). Có thể Anycast của Cloudflare đi tuyến khác cho TCP. Vì vậy chỉ dùng probe để biết "còn Internet hay không", không dùng làm số đo độ trễ.
- **Kết luận:** nguyên nhân chính gây rớt mạng là **ăng-ten card bị che bởi khung bàn kim loại/thùng máy** + các yếu tố cấu hình đã xử lý (modem phát Wi‑Fi, MLO). **Chưa cần mua AX3.** EXP-011/012 chuyển sang dự phòng.

## EXP-011 — Ăng-ten Wi‑Fi có dây nối dài cho card PCIe (dự kiến)

- **Bối cảnh:** PC ở **lầu trên**, router BE3 ở **lầu dưới**, không đi dây LAN được ⇒ giải thích tín hiệu chỉ −66…−80 dBm.
- **Đã đặt mua:** ăng-ten Wi‑Fi đế rời có dây nối dài, gắn vào card TP-Link (MT7922).
- **Yêu cầu khi mua/lắp:** đầu nối **RP-SMA** (đúng chuẩn card TP-Link); **2 ăng-ten** (card 2×2); băng tần **2.4 + 5 GHz**; dây **1–1.5 m** (dây mảnh suy hao ~1–2 dB/m ở 5 GHz, không nên dài quá); đặt đế cao, xa thùng máy/màn hình/kim loại.
- **Hướng ăng-ten khi router ở ngay tầng dưới:** ăng-ten đa hướng dựng đứng có "điểm mù" ngay phía trên/dưới ⇒ nghiêng ăng-ten ~45° hoặc nằm ngang ở cả hai đầu (PC và một ăng-ten của BE3).
- **Kỳ vọng:** cải thiện tín hiệu vài dB tới ~10 dB nếu ăng-ten hiện đang nằm sau thùng máy sát sàn. **Không** sửa được lỗi treo của driver MediaTek.
- **Đo:** so sánh RSSI và mất gói tới router trước/sau, cùng SSID `HomeNet_5G_Wi-Fi5` ch48.

## EXP-012 — Giải pháp chốt: thêm Huawei WiFi AX3 ở lầu trên, cable vào PC (dự kiến)

- **Quyết định của người dùng (2026-10-03):** mua thêm **Huawei WiFi AX3**, ghép mạng với BE3 Pro (HarmonyOS Mesh+), AX3 đặt ở lầu trên, **PC cắm dây LAN vào AX3** ⇒ loại bỏ hoàn toàn card MediaTek khỏi đường truyền.
- **Sơ đồ đích:**
  ```
  Modem (bridge, Wi‑Fi tắt) ── BE3 Pro (router chính, lầu dưới) ~~ backhaul 5G ~~ AX3 (node Mesh+, lầu trên) ── LAN ── PC
  ```
- **Lưu ý khi mua/lắp:**
  - BE3 Pro là **bản nội địa Trung Quốc** ⇒ ưu tiên AX3 **cũng bản nội địa** để chắc chắn ghép Mesh+ được (trộn bản quốc tế và nội địa có thể không ghép được — chưa kiểm chứng). Có người dùng báo AX3 ghép mesh được với BE3.
  - Phân biệt các biến thể AX3 (AX3 / AX3 Pro / AX3 New…); chọn bản có Mesh+ / 鸿蒙组网.
  - Ghép: đặt AX3 gần BE3 khi ghép lần đầu (nút H / app 智慧生活), xong mới mang lên lầu.
  - Vị trí AX3: nơi **nhận sóng BE3 tốt nhất ở lầu trên** (đầu cầu thang, ngay phía trên router), mục tiêu ≥ −65 dBm tại AX3; không nhất thiết đặt sát PC vì đã có dây.
  - **Cập nhật:** cửa sổ phòng làm việc là **cửa sổ trong nhà nhìn thẳng xuống phòng khách**, nơi đặt BE3 ⇒ có **đường nhìn thẳng (line-of-sight)** BE3 ↔ cửa sổ, không xuyên bê tông. Đường hiện tại tới PC phải xuyên **1 sàn + 1 mặt bàn**. ⇒ **Vị trí khuyến nghị: AX3 đặt ngay ở cửa sổ, bên trong phòng**, ăng-ten hướng vuông góc với đường thẳng tới BE3; BE3 đặt chỗ thoáng, nhìn thấy cửa sổ. Tránh khung/song cửa kim loại chắn ngay trước ăng-ten; kính dán phim/low‑E thì mở hé cửa.
  - Người dùng cân nhắc: (a) đặt **dưới sàn** phòng làm việc, hoặc (b) **treo ở cửa sổ**, thò ăng-ten ra ngoài. Khuyến nghị: **đo bằng điện thoại** tại các vị trí ứng viên (sàn ngay trên BE3, đầu cầu thang, cửa sổ) và chọn chỗ mạnh nhất. Sàn chỉ tốt khi **BE3 nằm gần như ngay bên dưới** (và BE3 nên đặt cao, gần trần lầu dưới). Cửa sổ chỉ đáng thử khi BE3 cũng ở sát cửa sổ cùng phía nhà; tránh để thiết bị ngoài trời (nắng, mưa, nóng).
  - "组网信道模式": với mesh không dây nên để **同信道部署** (cùng kênh) để backhaul hoạt động; giữ kênh 5G cố định (48) hoặc để router tự chọn sau khi mesh ổn định.
  - MLO vẫn **tắt** (đã thấy liên quan sự cố); mạng tương thích Wi‑Fi 5 có thể tắt sau khi PC dùng dây, nếu không còn thiết bị nào cần.
- **Đo:** logger tự chuyển theo gateway; kỳ vọng router mất gói ≈ 0%, không còn treo im lặng. Khi ổn định, có thể **tắt card Wi‑Fi MediaTek** (hoặc để làm dự phòng) và cân nhắc khôi phục EXP-001.
- **Ăng-ten nối dài (EXP-011):** trở thành phương án dự phòng; không còn cần thiết nếu EXP-012 thành công.

## Phát hiện: tình trạng xấu đi sau khi thay router (13:30)

Người dùng cho biết rớt mạng **nhiều hơn so với trước khi thay router**. Lịch sử event 8001 (log Windows từ 2026-09-08):

| Mạng | Số lần kết nối | Từ | Đến |
|---|---|---|---|
| OldModemNet 5G | 53 | 09-08 | 10-02 13:58 |
| OldModemNet (2.4G) | 7 | 09-21 | 10-02 13:59 |
| CNBN | 4 | 10-02 13:47 | 10-02 13:54 |
| NeighborNet | 8 | 10-02 13:57 | 10-02 15:35 |
| **HomeNet (BE3)** | 10 | **10-02 15:26** | nay |

Disconnect (8003) theo ngày: 09-08: 1 · 09-10: 8 · 09-11: 1 · 09-21: 5 · 09-26: 3 · 09-27: 3 · 09-28: 1 · 10-01: 6 · **10-02: 36** (phần lớn do chuyển qua lại giữa 4 mạng khi lắp router mới) · 10-03: 2.

⇒ **BE3 đưa vào dùng chiều 2026-10-02.** Trước đó PC dùng "OldModemNet 5G". Các mạng OldModemNet / CNBN / NeighborNet cùng họ MAC `c4/c6:2c:7b:…` (cùng một thiết bị/hãng) và **NeighborNet + một SSID ẩn đang ở kênh 36** — đúng kênh BE3 tự chọn.

Giả thuyết vì sao xấu hơn router cũ (xếp theo khả năng):
1. **Tương thích Wi‑Fi 7 (BE3) ↔ card MediaTek MT7922 (Wi‑Fi 6E)**: Rx sụp về 6 Mbps khi tín hiệu vẫn tốt là dấu hiệu điển hình của lỗi điều khiển tốc độ / tính năng mới (11be, OFDMA, TWT…) giữa router và driver. ⇒ thử tắt `be` trên 5G của BE3 (EXP-003b).
2. **Nhiễu cùng kênh 36** với thiết bị cũ còn đang phát gần đó ⇒ đang thử bằng EXP-002 (kênh 157).
3. **Vị trí BE3** xa/khuất hơn điểm phát cũ (RSSI −66 → −76 dBm).
4. EXP-001 (tắt power saving) — chưa loại trừ.

Lưu ý: Windows không ghi lại các lần "treo im lặng", nên không có số liệu so sánh tương đương cho giai đoạn router cũ.

## Cấu hình Wi‑Fi của router BE3 (ảnh chụp trang quản trị, 2026-10-03 ~11:50, firmware giao diện tiếng Trung)

| Thiết lập | 2.4G | 5G |
|---|---|---|
| Kênh (信道) | 自适应 — Tự động | 自适应 — Tự động (thực tế đang ở **kênh 36**) |
| Chuẩn (模式) | 802.11b/g/n/ax/be | 802.11a/n/ac/ax/be |
| Băng thông (频宽) | 20/40 MHz | 20/40/80 MHz (không dùng 160MHz ⇒ không có DFS) |
| 11be guard interval (前导间隔) | 短间隔 — ngắn | 短间隔 — ngắn |
| Ẩn SSID (隐身) | Tắt | Tắt |
| WMM | Bật | Bật |

Ghi chú của firmware: muốn chọn kênh thủ công phải tắt "自动优化信道" (tự động tối ưu kênh) và đặt "组网信道模式" (chế độ kênh khi ghép mạng) thành 同信道 (cùng kênh) / 异信道 (khác kênh). Có nút "一键优化信道" (tối ưu kênh một chạm). Chưa thấy mục công suất phát / xuyên tường trên trang này.

## Các thử nghiệm dự kiến

| ID | Nội dung | Điều kiện thực hiện |
|---|---|---|
| EXP-002 | Đổi kênh 5GHz trên router (36 → nhóm 149–161) | **Ưu tiên tăng** (11:20): mất kết nối trùng với Rx rate sụp dù tín hiệu tốt ⇒ nghi nhiễu kênh |
| EXP-001b | A/B: khôi phục EXP-001 trong 1–2 giờ, logger vẫn chạy | Để có baseline cùng thước đo, xác định EXP-001 tốt hơn hay tệ hơn |
| EXP-003b | Tắt Wi‑Fi 7 (`be`) trên 5G của BE3: 模式 → 802.11a/n/ac/ax | **Ưu tiên cao** nếu kênh 157 không cải thiện — nghi tương thích Wi‑Fi 7 ↔ MediaTek |
| EXP-003 | Rollback driver MediaTek 3.6.0.1434 → 25.40.2.585 (`oem18.inf`) | Nếu EXP-001 không giảm event 10002 |
| EXP-004 | `wifi_mode_ac` (ép 802.11ac) | Nếu vẫn crash sau EXP-003 |
| EXP-005a | **iGate 302S (chưa dùng) đặt gần PC ở chế độ repeater/wireless client của BE3, cable iGate → PC** (chi phí 0) | Trước tiên kiểm tra iGate có chế độ Repeater/Client hay chỉ có EasyMesh agent (cần controller VNPT). Loại bỏ driver MediaTek; còn chặng không dây iGate↔BE3 |
| EXP-005 | **Cắm dây tạm thời PC ↔ BE3** (Intel I219-V), vài giờ, logger vẫn chạy | **Nên làm sớm** — phép thử phân định: hết sự cố ⇒ lỗi ở chặng Wi‑Fi/card (mesh + cable sẽ giải quyết); còn sự cố ⇒ lỗi ở BE3/modem/ISP (mesh không giúp) |
| EXP-006 | Mesh: node thứ hai cạnh PC, cắm cable node → PC | Sau EXP-005 nếu xác nhận lỗi ở chặng Wi‑Fi. Node phải chạy **AP/bridge mode** (hoặc mesh Huawei Mesh+), không để router mode (tránh double NAT) |
