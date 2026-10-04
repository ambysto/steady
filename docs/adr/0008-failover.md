# ADR-0008: Failover sang đường mạng dự phòng bằng metric của interface

- **Trạng thái:** Accepted
- **Ngày:** 2026-10-04
- **Liên quan:** [ADR-0003](0003-tweak-framework.md) (sao lưu/khôi phục), [ADR-0004](0004-watchdog-safety.md) (giới hạn an toàn), [ADR-0005](0005-unelevated-server-uac-writes.md) (ghi cần Admin)

## Bối cảnh

Cải thiện ổn định rõ nhất đến từ việc có **≥ 2 đường ra Internet** (Wi‑Fi + LAN, USB 4G, điện thoại chia sẻ mạng…). Windows tự dùng đường khác khi một card **mất kết nối vật lý**, nhưng không làm gì khi card vẫn "Connected" mà Internet sau router đã chết (nhà mạng lỗi, router treo) — đúng ca hay gặp nhất. Speedify giải bằng server trung gian (giữ được phiên TCP); ta chọn cách nhẹ, không cần server: đổi thứ tự ưu tiên đường đi.

## Quyết định

1. **Đường (path)** = một card vật lý (không phải VPN/adapter ảo), đang Up, có default route IPv4 qua gateway. Danh sách đường đọc lại mỗi 60s; đường chính = đường Windows đang dùng (`GetBestRoute`).
2. **Đo từng đường riêng**: kết nối TCP tới `1.1.1.1:443` / `8.8.8.8:443` với socket **bind vào IP của đường đó** (Windows dùng mô hình strong host khi gửi, nên gói đi ra đúng card) mỗi 5s. Một lần thành công là đường còn dùng được.
3. **Chuyển (failover)** khi: đường chính hỏng liên tục ≥ `threshold_s` (20s) **và** có đường dự phòng khỏe liên tục ≥ 10s. Hành động: đặt **InterfaceMetric** của đường dự phòng đủ thấp để nó thắng (`Set-NetIPInterface`), không đụng đường chính.
4. **Quay về (failback)** khi đường chính khỏe liên tục ≥ `failback_s` (120s): khôi phục metric gốc của đường dự phòng. Tắt tính năng, monitor khởi động thấy còn metric đã đổi (crash), hay gỡ cài đặt ⇒ cũng khôi phục.
5. **Sao lưu trước khi ghi**: metric gốc (`AutomaticMetric`, `InterfaceMetric`) lưu vào `backup.json` khóa `failover:<ifIndex>` *trước* khi đổi; đọc lại để kiểm chứng; khôi phục xong mới xóa bản sao lưu (giống ADR-0003).
6. **Giới hạn an toàn** (giống ADR-0004, chính sách thuần, test được): giãn cách ≥ 60s giữa hai lần chuyển, tối đa 6 lần/giờ, chuyển qua lại ≥ 3 lần trong 30 phút ⇒ **ngắt mạch**: tự tắt tính năng, khôi phục metric, báo người dùng. Có chế độ `dry_run` chỉ ghi "sẽ làm gì". **Mặc định tắt.**
7. **Quyền Admin**: đổi metric cần Admin. Monitor chạy quyền Admin (task `--highest`) thì tự chuyển; nếu không, chỉ **báo** (sự kiện + toast "có đường dự phòng đang thông") và giao diện có nút "Chuyển ngay" — lúc đó UAC hỏi người dùng (ADR-0005). Không bao giờ bật hộp thoại UAC khi người dùng không chủ động bấm.
8. **Ai sở hữu lần chuyển** (`source` trong bản sao lưu): người dùng bấm "Chuyển ngay" khi failover đang **tắt** ⇒ `manual`: giữ nguyên cho tới khi người dùng chuyển về, chỉ tự hoàn tác nếu đường đó hỏng. Failover tự chuyển, hoặc người dùng bấm khi failover **bật** ⇒ `failover`: tự quay về như mục 4.
9. **Chuyển/khôi phục thất bại** (adapter bị rút, chính sách chặn…): báo **một lần**, thử lại sau 60s, 5 phút, rồi 15 phút, không thử mỗi tick. Bản sao lưu giữ nguyên cho tới khi khôi phục được; Windows lưu metric theo interface nên cắm lại là khôi phục được, và giao diện vẫn có nút "Chuyển về" cho đường đã rút.
10. **Đo không chắc chắn ≠ hỏng**: bind vào IP cũ thất bại (DHCP cấp IP mới) ⇒ "chưa biết", đọc lại danh sách đường ngay; máy ngủ (hai tick cách > 30s) ⇒ quên các khoảng "khỏe/hỏng bao lâu" để không chuyển nhầm sau khi thức.
11. `backup.json` được monitor, tiến trình Admin và trình gỡ cài đặt cùng ghi: mỗi lần ghi giữ khóa liên tiến trình (`config.backup_lock`), đọc lại file ngay trong khóa và chỉ đổi đúng mục của mình.

## Hệ quả

- ✅ Không cần server, không thêm thư viện; giữ đường chính nguyên vẹn.
- ⚠️ Phiên TCP đang mở trên đường cũ sẽ rớt khi chuyển (ứng dụng tự kết nối lại); khác Speedify.
- ⚠️ Đường dự phòng tính phí (4G) có thể tốn data — giao diện ghi rõ đường nào đang được dùng.
- ⚠️ Máy chỉ có một đường thì tính năng không làm gì (báo "chưa có đường dự phòng").
- 📌 Không áp dụng lên máy phát triển nếu người dùng chưa đồng ý (CLAUDE.md).
