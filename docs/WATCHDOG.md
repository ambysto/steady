# Watchdog — tự khôi phục

Mặc định **tắt**. Bật trong tab Tối ưu (nhóm "Tính năng của tool"), hoặc trong `data/settings.json` (`"watchdog": {"enabled": true}`). Thiết kế an toàn: [ADR-0004](adr/0004-watchdog-safety.md).

Watchdog chạy **bên trong tiến trình monitor**, đọc trạng thái sống của monitor mỗi giây, và chạy hành động trong một luồng riêng để không chặn việc đo.

## Phát hiện sự cố

| Tình huống | Điều kiện |
|---|---|
| `wifi_down` | Card Wi‑Fi ở trạng thái `disconnected` ≥ `threshold_s` (mặc định 15s) |
| `router_unreachable` | Ping router thất bại liên tục ≥ `threshold_s`. Gồm cả **treo im lặng**: Wi‑Fi vẫn báo `connected`, cùng BSSID, nhưng không có dữ liệu (EXP-001 57s, EXP-010 ~641s) |
| *(không xử lý)* | Router OK nhưng mất Internet ⇒ lỗi phía router/ISP, khởi động lại card không giúp được — chỉ ghi nhật ký |

**Không can thiệp** khi:
- uplink hiện tại **không phải** card Wi‑Fi: route mặc định đi qua một card vật lý khác đang Up, như dây mạng, điện thoại chia sẻ qua USB (media "Unspecified") hay USB 4G. Lúc đó PC vẫn có mạng, nên reset Wi‑Fi chỉ cắt một đường không ai dùng. Card ảo (VPN) vẫn tính là Wi‑Fi. Card mới cắm chưa có trong danh sách thì danh sách được đọc lại ngay, mỗi card một lần (lỗi tìm ra 2026-10-04 khi test failover với iPhone);
- lần ngắt Wi‑Fi gần nhất là **do người dùng** (event 8003 "disconnected by the user") — không giành lại kết nối người dùng vừa ngắt;
- trong **60s sau khi máy thức dậy** (sự kiện `monitor_gap`): mạng đang tự kết nối lại;
- trong **120s sau khi một tweak thay đổi máy**: card khởi động lại do tweak không phải sự cố;
- ngắt mạch đang mở (xem dưới).

## Thang hành động

| Bước | `wifi_down` | `router_unreachable` |
|---|---|---|
| 1 | **Kết nối lại** profile gần nhất: `netsh wlan connect` | **Kết nối lại cưỡng bức**: `netsh wlan disconnect` rồi `connect` (buộc liên kết lại, không cần Admin) |
| 2 | **Khởi động lại card**: `Restart-NetAdapter` (🛡 Admin) | **Khởi động lại card** (🛡 Admin) |

Thang được thử **một lần cho mỗi sự cố**; thử hết mà chưa khỏi thì chờ, không lặp lại. Thiếu quyền Admin thì bỏ qua bước 2 và ghi lý do (task tự khởi động mặc định chạy quyền thường; cài với `--highest` để watchdog dùng được bước 2).

## Giới hạn an toàn

| Thiết lập (`settings.json` → `watchdog`) | Mặc định | Mục đích |
|---|---|---|
| `threshold_s` | 15 | Sự cố phải kéo dài bao lâu mới can thiệp |
| `cooldown_s` | 120 | Khoảng cách tối thiểu giữa hai hành động |
| `max_per_hour` | 6 | Trần số hành động trong 60 phút trượt, **đếm cả các lần trước khi tiến trình khởi động lại** (đọc từ nhật ký) |
| `verify_s` | 60 | Sau mỗi hành động chờ chừng này; sự cố chưa hết ⇒ hành động bị tính là **không hiệu quả** |
| `trip_after` | 3 | Số hành động không hiệu quả **liên tiếp** để mở ngắt mạch |
| `dry_run` | false | Chỉ ghi "sẽ làm gì", không thực hiện — dùng để quan sát trước khi bật thật |

### Chọn ngưỡng (`threshold_s`)

Phát lại 257 lần mất router đã ghi ngày 2026-10-03 (không hành động nào được giả định là có tác dụng):

| Ngưỡng | Sự cố đạt ngưỡng | Trong đó tự hết trong 60s sau khi can thiệp | Số hành động trong ngày | Phần thời gian mất mạng được xử lý |
|---|---|---|---|---|
| 15s | 68 | 49 (72%) | 31 | 83% |
| 30s | 46 | 32 (70%) | 24 | 74% |
| **45s** | **29** | **16 (55%)** | **20** | **64%** |
| 60s | 25 | 13 (52%) | 17 | 61% |

Ở 15–30s, phần lớn can thiệp rơi vào sự cố đằng nào cũng tự hết, mà mỗi lần lại tự gây mất mạng vài giây. **Máy phát triển dùng 45s, chạy `dry_run` từ 2026-10-04 10:20** để đối chiếu trước khi bật thật. Mặc định trong code vẫn là 15s cho tới khi có số liệu chạy thử.

**Ngắt mạch:** khi mở, watchdog tự tắt (`enabled` = false, ghi `tripped_at` vào `settings.json`) và ghi sự kiện mức `bad`. Chỉ người dùng bật lại mới chạy tiếp — kể cả khi tiến trình khởi động lại.

## Thông báo (toast)

Mặc định bật (`settings.json` → `notify.enabled`, đọc lại mỗi 10 giây; công tắc cũng có ở `/api/settings`). Toast hiện cho:

| Khi | Nội dung |
|---|---|
| Sự cố mạng kéo dài ≥ 30s (`notify.outage_after_s`) | "Mất kết nối tới router" hoặc "Mất Internet", kèm thời gian đã mất |
| Sự cố đó kết thúc | "Đã có mạng trở lại", kèm thời gian — chỉ khi đã báo lúc mất |
| `watchdog_action`, `watchdog_tripped`, `watchdog_error` | Nội dung sự kiện nhật ký |

Không có toast cho: sự cố ngắn hơn 30s, chế độ `dry_run`, các lần bỏ qua, và "đã khôi phục" sau khoảng trống khi máy ngủ (không biết chuyện gì đã xảy ra nên không đoán). Giới hạn: cùng tiêu đề tối đa 1 lần/30s, tối đa 6 toast/10 phút. Toast mang tên "Windows PowerShell" vì script không có AppUserModelID riêng.

## Nhật ký

| Sự kiện | Mức | Khi nào |
|---|---|---|
| `watchdog_action` | warn | Thực hiện (hoặc "sẽ thực hiện" khi `dry_run`) một hành động, kèm lý do |
| `watchdog_recovered` | info | Sự cố hết sau hành động, kèm thời gian |
| `watchdog_ineffective` | warn | Hành động không giúp được sau `verify_s` |
| `watchdog_skip` | info | Không can thiệp dù có sự cố (thiếu Admin, đạt trần, ngắt do người dùng…) — chỉ ghi khi lý do thay đổi |
| `watchdog_tripped` | bad | Mở ngắt mạch, watchdog đã tự tắt |
| `watchdog_error` | bad | Hành động thất bại (lệnh lỗi) |
