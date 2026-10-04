# Chẩn đoán

Mỗi kiểm tra trả về một kết quả: `ok` · `warn` · `bad` · `info`, kèm chi tiết và gợi ý (có thể liên kết tới tweak tương ứng). Kiểm tra không chạy được (thiếu dữ liệu, log không đọc được) trả `info` kèm lý do — **không bao giờ** báo `ok` khi chưa đọc được dữ liệu.

| # | Kiểm tra | Nguồn dữ liệu | Ngưỡng / đánh giá | Gợi ý |
|---|---|---|---|---|
| 1 | **Driver card mạng** | `Get-NetAdapter`, `pnputil /enum-drivers /class Net`, System log `WLAN-AutoConfig 10002` (IHV module dừng) | Driver cũ > 12 tháng ⇒ warn; có crash IHV module trong 7 ngày ⇒ bad | Cập nhật / rollback driver; liệt kê các bản cùng nhà cung cấp trong driver store |
| 2 | **Tín hiệu Wi‑Fi** | `netsh wlan show interfaces` | RSSI ≥ −60 ok · −61…−70 warn · < −70 bad | Dời router/PC, dùng 5GHz, mesh, hoặc cắm LAN |
| 3 | **Nhiễu kênh** | `netsh wlan show networks mode=bssid` | ≥ 2 mạng khác cùng kênh ⇒ warn. Bỏ qua mạng tín hiệu < 10% và các BSSID cùng thiết bị với AP đang dùng (5 octet MAC đầu trùng) | Gợi ý nhóm kênh 5GHz ít mạng nhất (36–48 / 149–161) |
| 4 | **Lịch sử rớt mạng (7 ngày)** | `WLAN-AutoConfig/Operational` event 8003 (theo lý do, theo ngày), event 4003 (limited connectivity) | > 5 lần "disconnected by the driver" ⇒ bad; 1–5 lần hoặc có event 4003 ⇒ warn. Không tính ngắt do người dùng / do kết nối mới | Tweak nguồn điện, driver, watchdog |
| 5 | **Chất lượng ping** | Dữ liệu của monitor, cửa sổ 1 giờ (kèm 5 phút trong chi tiết), cần ≥ 60 mẫu mỗi mục tiêu ping và ≥ 20 mẫu probe. Probe = kết nối TCP:443 và HTTP 204 (`tcp_*`, `http_*`), không phụ thuộc ICMP | Mất gói > 1% warn, > 3% bad · jitter trung bình > 30ms warn. **Ping ra Internet mất gói nhưng probe tốt nhất mất ≤ 1% ⇒ chỉ `info` "ICMP bị giới hạn"** (không phải mất mạng). Chỉ cần một probe tốt: một đích chặn cổng 443 không gây báo động | Phân biệt lỗi nội bộ (router mất) vs ISP (router tốt, probe mất) vs ping bị giới hạn |
| 6 | **Benchmark DNS** | Truy vấn UDP tới: DNS đang dùng, router, 1.1.1.1, 8.8.8.8, 9.9.9.9 × nhiều tên miền phổ biến | DNS chính chậm hơn DNS nhanh nhất > 20ms (trung vị) ⇒ info; lỗi/timeout ở **DNS đang dùng** ⇒ warn (ở DNS tham chiếu chỉ ghi vào chi tiết). Router trả lời nhanh nhờ bộ nhớ đệm nên số đo của nó lạc quan | Đổi DNS, hoặc đưa DNS nhanh hơn đã cấu hình lên làm chính |
| 7 | **Cạn port TCP** | Tcpip event 4227 (7 ngày), số kết nối TIME_WAIT | Có event 4227 ⇒ warn | Tweak `tcp_timedwait` |
| 8 | **VPN / adapter ảo** | `Get-NetAdapter` (WireGuard, Wintun, TAP, OpenVPN, NetBird, Surfshark, Tailscale…) | info nếu có adapter VPN đang `Up` | Cảnh báo VPN có thể ảnh hưởng định tuyến/DNS |
| 9 | **Mạng dây khả dụng** | Adapter vật lý 802.3 đang `Disconnected` | info | Cắm LAN là giải pháp ổn định nhất |
| 10 | **Tối ưu chưa bật** | Trạng thái các tweak rủi ro `low` (từ khung tweak) | Còn tweak low chưa bật ⇒ info. Chưa có khung tweak ⇒ info "chưa đánh giá được" | Liên kết sang tab Tối ưu |
| 11 | **Tương thích Wi‑Fi 7 / MLO** | `show interfaces` (Radio type), `show networks` (BSSID 802.11be của cùng thiết bị), model card, lịch sử 24h của monitor | Card không hỗ trợ Wi‑Fi 7 + router phát 802.11be + **sụp Rx đi kèm mất gói** ⇒ warn. Có 802.11be nhưng chưa thấy sụp ⇒ info. Xem "Hiệu chỉnh" | Tắt MLO trên router, hoặc dùng SSID tương thích Wi‑Fi 5/6. Bài học từ EXP-009 (2026-10-03) |
| 12 | **Wi‑Fi của modem nhà mạng ở chế độ bridge** | Các SSID lạ, mạnh, cùng họ MAC (bỏ bit locally-administered) trong kết quả quét | ≥ 2 BSSID cùng họ MAC (kể cả SSID ẩn) với tín hiệu cao nhất ≥ 70% ⇒ warn; ≥ 50% ⇒ info. Một mạng lạ đơn lẻ ≥ 70% ⇒ info. Không kiểm tra được "có Internet hay không" từ kết quả quét | Tắt Wi‑Fi modem, đặt router cách ≥ 1 m. Bài học từ EXP-008 |
| 13 | **Đường truyền vật lý kém (ăng-ten bị che / vị trí)** | Lịch sử 24h của monitor: Rx rate, RSSI, mất gói tới router | Tỉ lệ phút có **Rx ≤ 30 Mbps và router mất gói ≥ 5%**: ≥ 20% ⇒ warn, ≥ 5% ⇒ info (cần ≥ 30 phút dữ liệu kết nối). RSSI trung vị < −76 ⇒ nêu rõ tín hiệu yếu là yếu tố chi phối. Xem "Hiệu chỉnh" | Loại trừ #11, #12, #3 trước. Nếu sạch: ăng-ten card có thể bị che (khung bàn kim loại, thùng máy) — xoay/dời, hoặc dùng ăng-ten nối dài. Bài học từ EXP-014 |

## Kiểm tra theo yêu cầu (không nằm trong lần chạy mặc định)

Kiểm tra này **tạo lưu lượng thật** (tốn dữ liệu, làm chậm mạng vài chục giây) nên chỉ chạy khi người dùng yêu cầu: `python -m app.diagnostics --bufferbloat` hoặc `POST /api/diagnostics/bufferbloat`.

| # | Kiểm tra | Nguồn dữ liệu | Ngưỡng / đánh giá | Gợi ý |
|---|---|---|---|---|
| 14 | **Bufferbloat** (độ trễ tăng vọt khi đường truyền bận) | Ping router và 1.1.1.1 mỗi 0,2s: 4s lúc rảnh, rồi 10s trong khi tải xuống, rồi 10s trong khi tải lên (4 kết nối song song tới `speed.cloudflare.com`, mỗi yêu cầu 25 MB và lặp lại; mỗi chiều dừng ở 10s hoặc 100 MB tổng — Cloudflare từ chối một yêu cầu lớn hơn ~25 MB). Bỏ 2s đầu mỗi pha (đường truyền đang lấy đà) | Độ trễ **trung vị** tăng thêm so với lúc rảnh, lấy mức tệ nhất giữa hai chiều: < 30 ms ok · 30–100 ms warn · > 100 ms bad. Mất ≥ 20% ping trong lúc tải ⇒ bad. Không tạo được tải (lỗi mạng, < 1 Mbps) hoặc lúc rảnh không có phản hồi ⇒ info | Bật SQM / QoS thông minh (fq_codel, CAKE) trên router và đặt giới hạn tốc độ tải lên/xuống thấp hơn băng thông thật ~5–10%. Tăng cả tới router ⇒ hàng đợi ở chặng PC ↔ router (Wi‑Fi bão hòa hoặc chính router quá tải); chỉ tăng tới đích Internet mà router không tăng ⇒ hàng đợi ở chặng WAN (modem/router → nhà mạng) |

Lưu ý: ngưỡng lấy từ chính yêu cầu của ticket (SIC-37), chưa hiệu chỉnh trên số liệu thật. Bufferbloat đo ở đây là của **cả đường**; Wi‑Fi tự nó cũng gây tăng trễ khi bão hòa, nên số đo tới router được báo riêng để phân biệt.

## Ghi chú cài đặt

- Các kiểm tra dựa trên cửa sổ dài (#1, #4: 7 ngày; #11, #13: 24 giờ) luôn ghi "gần nhất bao lâu trước". Với #11 và #13, nếu **3 giờ gần nhất** (≥ 15 phút dữ liệu) đã sạch (< 5% phút bất thường) thì kết quả hạ xuống `info` "đã cải thiện?" — để việc vừa sửa xong không bị báo `warn` mãi vì dữ liệu cũ. #1 và #4 giữ đúng ngưỡng 7 ngày.

- Các truy vấn event log gom vào **một** lệnh PowerShell để giảm thời gian khởi động process. Event log "không tìm thấy sự kiện" là kết quả rỗng bình thường; mọi lỗi khác (không đọc được log) được báo lại.
- DNS benchmark làm bằng Python (UDP thô), không phụ thuộc cache của Windows.
- Bỏ qua DNS IPv6 link-local (`fe80::`) vì cần scope id.
- Kết quả chẩn đoán được lưu (bảng `diagnostic_runs`, kèm thời điểm) để so sánh giữa các lần chạy.
- Chữ trong kết quả (`title`, `summary`, `details`, `advice`) được lưu dạng thông điệp `{key, params}` (khóa `diag.*` trong `app/locales/*.json`, ADR-0006) và dịch lúc đọc: API trả chữ theo `ui.language` (hoặc `?lang=`), CLI có `--lang`. Lần chạy lưu trước khi chuyển (chữ thuần tiếng Việt) hiển thị nguyên văn. Chuỗi không có chữ (tên card, `RSSI -65 dBm`) vẫn lưu dạng chữ thường.
- Kết quả chẩn đoán sinh **gợi ý** (`app/suggestions.py`): tweak liên quan và việc làm bằng tay. Bảng việc làm bằng tay ↔ kiểm tra kích hoạt: `antenna` ← #13 warn/thỉnh thoảng sụp; `move_closer` ← #2 warn/bad; `modem_wifi_off` ← #12; `router_channel` ← #3 warn; `router_mlo_off` ← #11 warn; `driver_update` ← #1 warn/bad; `use_cable` ← #9 có cổng LAN trống; `router_sqm` ← #14 warn/bad; `dns_server` ← #6 warn.
- Chỉ đọc: chẩn đoán không thay đổi cấu hình máy và không kích hoạt quét Wi‑Fi mới (dùng kết quả quét gần nhất của Windows).

## Hiệu chỉnh ngưỡng (#11, #13)

Rx rate của `netsh` là tốc độ khung gần nhất và **không đáng tin khi lưu lượng thấp** (EXP-009: Rx hiển thị 6 Mbps nhiều phút mà không mất gói). Vì vậy không kiểm tra nào dùng Rx một mình: luôn kèm mất gói tới router trong cùng phút.

Các ngưỡng được thử trên số liệu thật của máy phát triển ngày 2026-10-03 (phút kết nối, ≥ 30 gói, tỉ lệ phút thỏa điều kiện):

| Khoảng | Mô tả | Rx ≤ 30 & mất ≥ 5% (#13) | Rx ≤ 6 & RSSI ≥ −70 & mất ≥ 5% (#11) |
|---|---|---|---|
| 11:30–13:14 | MLO bật, kênh 36 (mất gói ~35%) | 73,1% | 52,9% |
| 14:02–14:41 | MLO tắt, Rx hiển thị 6 nhưng ít mất gói | 53,8% | 2,6% |
| 14:52–15:37 | 2.4GHz | 87,8% | 14,6% |
| 15:43–16:22 | Wi‑Fi 6, kênh 40 | 23,1% | 10,3% |
| 16:27–17:26 | Ép Wi‑Fi 5 | 44,1% | 18,6% |
| 17:20–17:31 | Trước khi xoay thùng máy (ăng-ten bị che) | 54,5% (chỉ 11 phút) | 0% |
| 18:44–20:40 | Sau khi xoay thùng máy | **0,0%** | 0,0% |
| 23:08–08:41 | Qua đêm sau khi xoay | **0,0%** | 0,0% |

Kết luận khi chọn ngưỡng:
- **#13** tách rất rõ lúc hỏng (23–88%) với lúc tốt (0,0%), nên ngưỡng warn 20% / info 5% hợp lý. Nhưng nó **không phân biệt được nguyên nhân** (MLO, 2.4GHz, ăng-ten, nhiễu đều cho kết quả tương tự) — nên kiểm tra này là bộ phát hiện *triệu chứng* và gợi ý thứ tự loại trừ, không khẳng định "ăng-ten bị che".
- **#11** chỉ coi là lịch sử *bổ trợ* cho bằng chứng cấu trúc (card không Wi‑Fi 7 + router phát 802.11be). Ngưỡng warn là ≥ 25% số phút và ≥ 5 phút: chỉ khoảng MLO bật (52,9%) vượt; các khoảng không MLO nhưng vẫn tệ (10–19%) thì không.
- Cỡ mẫu nhỏ (một máy, một ngày): coi các ngưỡng là điểm khởi đầu, cần chỉnh khi có thêm dữ liệu.
