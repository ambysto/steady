# Phiên bản điện thoại & máy tính bảng (iOS / Android) — phân tích chuẩn bị

> Trạng thái: **nghiên cứu, chưa quyết định** (2026-10-04). Các điểm đánh dấu ⚠️ cần xác minh bằng prototype trên máy thật trước khi cam kết.

## 1. Bài toán người dùng

Trên điện thoại/tablet, mạng chậm hay chập chờn mà không biết vì sao, ví dụ:

- Đang ở Wi‑Fi 5 GHz, đi sang phòng khác tín hiệu yếu nhưng máy **không chuyển sang 2.4 GHz** (hoặc sang nút mesh gần hơn).
- **VPN** (hoặc "DNS riêng", proxy) đang bật làm chậm hoặc chặn.
- **Ứng dụng chạy nền** (sao lưu ảnh, cập nhật, cloud sync) chiếm băng thông.
- Router ổn nhưng nhà mạng lỗi, DNS chậm, mạng bị giới hạn (Low Data Mode / Data Saver), captive portal, bufferbloat…

Mong muốn: **một chạm để biết chuyện gì đang xảy ra, một chạm để sửa.**

## 2. Thực tế nền tảng: hệ điều hành cho phép gì

Khác biệt lớn nhất so với bản Windows: trên mobile **app gần như không được tự đổi cấu hình mạng**. "1 click to optimize" trên mobile thực tế là: *1 chạm chẩn đoán* → danh sách nguyên nhân có bằng chứng → mỗi nguyên nhân có *nút đưa thẳng tới chỗ sửa* (hoặc tự làm nếu OS cho phép). Hứa "tăng tốc mạng" mà không làm được là lý do bị App Store / Google Play từ chối.

| Vấn đề | Android | iOS / iPadOS |
|---|---|---|
| Tín hiệu (RSSI), băng tần (2.4/5/6 GHz), tốc độ liên kết, BSSID | ✅ `WifiInfo` qua `NetworkCapabilities` (cần quyền vị trí) | ❌ không có RSSI/băng tần. Chỉ SSID/BSSID (`NEHotspotNetwork.fetchCurrent`, cần entitlement "Access Wi‑Fi Information" + quyền vị trí chính xác) |
| "Kẹt" ở 5 GHz yếu trong khi 2.4 GHz/nút mesh khác mạnh hơn | ✅ phát hiện được: RSSI hiện tại + quét mạng (bị giới hạn ~4 lần/2 phút) | ⚠️ chỉ suy ra gián tiếp: độ trễ/mất gói tới router tăng, tốc độ tải giảm. Không thấy băng tần |
| Ép chuyển băng / sang AP khác | ❌ không ép được (máy tự quyết, nhất là khi 2 băng chung SSID). ⚠️ Nếu router có SSID riêng cho 2.4 GHz: gợi ý kết nối bằng `WifiNetworkSuggestion`/`WifiNetworkSpecifier` (người dùng xác nhận) | ❌. ⚠️ `NEHotspotConfiguration` mời kết nối vào SSID đã biết (người dùng xác nhận) |
| VPN đang bật | ✅ `NetworkCapabilities.TRANSPORT_VPN`; mở thẳng màn cài đặt VPN | ✅ phát hiện được (giao diện `utun/ipsec` trong cấu hình proxy hệ thống) ⚠️; chỉ hướng dẫn tắt |
| App nền chiếm băng thông | ✅ dữ liệu theo từng app qua `NetworkStatsManager` (người dùng phải cấp quyền "Truy cập dữ liệu sử dụng" trong Cài đặt) | ❌ không có số liệu theo app. Chỉ biết tổng lưu lượng của máy ⚠️ |
| Data Saver / Low Data Mode, mạng tính phí | ✅ `getRestrictBackgroundStatus`, `NOT_METERED` | ✅ `NWPath.isConstrained`, `isExpensive` |
| Router hay nhà mạng lỗi (ping router vs Internet), DNS chậm, mất gói, bufferbloat, captive portal | ✅ đo trực tiếp (ICMP, TCP, HTTP, DNS) | ✅ đo trực tiếp (ICMP qua socket datagram như mẫu SimplePing của Apple, TCP, HTTP, DNS) |
| Nhiễu kênh (bao nhiêu mạng cùng kênh) | ✅ từ kết quả quét | ❌ |
| Bật/tắt Wi‑Fi, quên mạng, đổi DNS hệ thống | ❌ từ Android 10 không tự bật/tắt; ✅ mở bảng "Settings Panel" để người dùng bấm. DNS riêng: chỉ hướng dẫn | ❌ không. Deep link vào trang Cài đặt cụ thể là API riêng ⇒ bị từ chối khi review |
| Theo dõi 24/7 như bản Windows | ⚠️ cần foreground service (có thông báo thường trực), bị tiết kiệm pin hạn chế | ❌ không chạy nền liên tục; chỉ đo khi mở app (+ BGTask ngắn, không đảm bảo) |

Hệ quả: **Android làm được gần đủ** bộ chẩn đoán; **iOS chỉ làm được phần đo mạng** (router/Internet/DNS/bufferbloat/VPN/Low Data Mode), không có tín hiệu, băng tần hay app nền.

### Lựa chọn mạnh hơn (để sau, không đề xuất cho bản đầu)
Một "VPN cục bộ" (`VpnService` trên Android, `NetworkExtension` trên iOS) cho phép thấy lưu lượng theo app trên Android và tự đổi DNS trên cả hai — cách các app như 1.1.1.1/NetGuard làm. Đổi lại: xung đột với VPN người dùng đang dùng, tốn pin, iOS cần entitlement Network Extension và review khắt khe hơn. Chỉ cân nhắc khi bản đầu đã chứng minh giá trị.

## 3. Trải nghiệm đề xuất: "Kiểm tra mạng" một chạm

1. Bấm **Kiểm tra** (~15–30 giây): đo router, Internet, DNS, tốc độ ngắn, (Android) tín hiệu/băng tần/quét, VPN, Data Saver, app nền.
2. Kết quả theo dạng thẻ giống màn Chẩn đoán hiện có: *"Bạn đang ở 5 GHz với tín hiệu yếu (−78 dBm), cùng mạng ở 2.4 GHz mạnh hơn"* → nút **Tắt/bật Wi‑Fi** (mở bảng Wi‑Fi để máy chọn lại AP) hoặc hướng dẫn *"Tách SSID 2.4/5 GHz trên router"*.
3. **Sửa một chạm** = làm *những gì OS cho phép* theo thứ tự; còn lại mở đúng màn cài đặt kèm một dòng hướng dẫn.
4. Lưu lịch sử các lần kiểm tra để so trước/sau (giống ADR-0007), không gửi dữ liệu đi đâu.

## 4. Tái sử dụng từ bản Windows

| Thành phần | Dùng lại thế nào |
|---|---|
| Giao diện (`web/`, đã co giãn tới khổ điện thoại), `tokens.css` | Bọc bằng **Capacitor** (WebView + plugin native) — giữ nguyên HTML/CSS/JS, thêm điều hướng dạng tab dưới cho mobile |
| Bản dịch `app/locales/*.json` (9 ngôn ngữ) | Dùng nguyên |
| Luật chẩn đoán (`app/diagnostics.py`), gợi ý, đo hiệu quả (ADR-0007) | Viết lại bằng TypeScript chạy trên máy (mobile không có server Python). Giữ **chung bộ ca kiểm thử** dạng JSON (đầu vào → kết quả mong đợi) để hai bản không lệch nhau |
| Monitor, tweak Windows, watchdog | Không dùng được (khác hệ điều hành) |

Phần native cần viết: plugin Kotlin (Wi‑Fi info, quét, per-app usage, Data Saver, settings panels, foreground service) và Swift (Wi‑Fi SSID/BSSID, NWPathMonitor, ICMP).

Phương án khác đã cân nhắc: Flutter hoặc native thuần (Kotlin + Swift) cho cảm giác native hơn nhưng phải viết lại toàn bộ giao diện ⇒ chọn lại khi Capacitor không đáp ứng hiệu năng/UX (quyết định chốt bằng ADR sau prototype).

## 5. Cần chuẩn bị

**Tài khoản & pháp lý**
- Apple Developer Program (99 USD/năm), Google Play Console (25 USD, một lần). Tài khoản tổ chức nếu phát hành dưới tên công ty (cần D‑U‑N‑S).
- Chính sách quyền riêng tư (bắt buộc vì dùng quyền vị trí để đọc SSID), Google Play Data safety, Apple Privacy Nutrition Label. Nguyên tắc: không thu thập, mọi dữ liệu ở trên máy.
- Kiểm tra tên "Ambysto Steady" trên hai store và nhãn hiệu.

**Thiết bị & công cụ**
- **Máy Mac** (Xcode bắt buộc để build/ký iOS) hoặc dịch vụ CI có macOS.
- Máy thật: ≥ 1 iPhone, 1 iPad, 2–3 Android khác hãng (Samsung, Pixel, Xiaomi — mỗi hãng tiết kiệm pin khác nhau), router 2 băng / mesh để tái hiện ca "kẹt 5 GHz".
- Android Studio, Node (đã có), Capacitor CLI.

**Thiết kế**
- Biến thể giao diện mobile: tab dưới, kích thước chạm ≥ 44 pt, safe area; bố cục tablet 2 cột (mockup hiện tại đã gần đúng).
- Icon app, ảnh chụp màn hình store cho từng kích cỡ, mô tả store 9 ngôn ngữ (đã có catalog).
- Giải thích quyền trước khi xin (vì sao cần vị trí, vì sao cần "truy cập dữ liệu sử dụng").

## 6. Lộ trình đề xuất

| Giai đoạn | Nội dung | Ước lượng |
|---|---|---|
| 0. Spike | App Android tối thiểu (Capacitor + plugin Kotlin) đọc RSSI/băng tần/quét/VPN/per-app usage trên 2–3 máy thật; đo ICMP trên iOS. Xác minh các ô ⚠️ ở bảng trên | 1–2 tuần |
| 1. Lõi chung | Chuyển luật chẩn đoán + gợi ý sang TypeScript, bộ ca kiểm thử JSON dùng chung với bản Windows | 2 tuần |
| 2. Android MVP | "Kiểm tra một chạm", thẻ kết quả, sửa có hướng dẫn, lịch sử | 2–3 tuần |
| 3. iOS MVP | Phần đo mạng + VPN + Low Data Mode (không có Wi‑Fi chi tiết) | 2 tuần |
| 4. Tablet + phát hành | Bố cục 2 cột, store listing, review | 1–2 tuần |

Quyết định cần chốt trước khi bắt đầu: (1) có làm iOS ngay không khi iOS chỉ làm được ~50% chẩn đoán; (2) Capacitor hay native (sau spike); (3) có dùng "VPN cục bộ" ở bản sau không.
