# ADR-0004: Watchdog — quyết định thuần, giới hạn bền vững, ngắt mạch tự tắt

- **Trạng thái:** Accepted
- **Ngày:** 2026-10-04

## Bối cảnh

Watchdog ngắt mạng có chủ đích (kết nối lại, khởi động lại card). Sai một điều kiện là thành vòng lặp tự gây mất mạng. Dữ liệu thật cho thấy: treo im lặng không có event Windows nào (EXP-001, EXP-010); mất gói ra Internet đều đặn 8–9% trong khi router tốt (EXP-014, nghi ICMP bị giới hạn); máy ngủ/thức tạo khoảng trống số liệu; tweak làm card khởi động lại. Monitor chạy qua Task Scheduler với quyền thường.

## Quyết định

1. **Tách quyết định khỏi thực thi.** `WatchdogPolicy` là hàm thuần nhận một quan sát (trạng thái Wi‑Fi và thời điểm đổi, sự cố router đang mở, uplink, quyền Admin, thời gian ân hạn) và trả về hành động hoặc lý do bỏ qua. Mọi giới hạn an toàn nằm ở đây và được kiểm bằng phát lại (replay) số liệu thật. Thực thi (`app/actions.py`) là module duy nhất ngắt mạng.
2. **Chỉ phản ứng với lỗi phía PC↔router** (`wifi_down`, `router_unreachable`). Mất Internet khi router tốt không bao giờ dẫn tới hành động.
3. **Giới hạn bền vững qua khởi động lại:** trần số lần/giờ được tính cả từ nhật ký trong DB, nên một tiến trình crash và tự khởi động lại liên tục không thể vượt trần.
4. **Ngắt mạch tự tắt watchdog** (ghi vào `settings.json`) sau `trip_after` hành động không hiệu quả liên tiếp; chỉ người dùng bật lại.
5. **Bước 1 luôn không cần Admin** (kết nối lại; với treo im lặng thì ngắt rồi kết nối lại), vì monitor mặc định chạy quyền thường. Khởi động lại card là bước 2 và cần Admin.
6. **Thời gian ân hạn** sau khi máy thức (60s) và sau khi tweak đổi máy (120s); không giành lại kết nối người dùng vừa ngắt.
7. **Chế độ `dry_run`** ghi quyết định mà không thực hiện — cách an toàn để quan sát trên máy thật trước khi bật.

## Hệ quả

- ✅ Toàn bộ logic an toàn kiểm được bằng đồng hồ giả và phát lại sự cố đã ghi.
- ✅ Không có đường nào để watchdog chạy quá `max_per_hour` lần/giờ, kể cả qua crash-loop.
- ⚠️ Không có Admin thì watchdog chỉ kết nối lại được; nếu treo im lặng cần khởi động lại card, phải cài task với `--highest`.
- ⚠️ Kết nối lại cưỡng bức ngắt mạng vài giây ngay cả khi treo im lặng tự hết đúng lúc đó — chấp nhận được vì đã qua ngưỡng 15s.
