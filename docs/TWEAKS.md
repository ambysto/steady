# Danh mục tối ưu (Tweaks)

Mỗi tweak là một toggle có 4 thao tác: **read** (đọc trạng thái) · **capture** (lưu giá trị gốc) · **apply** (bật) · **restore** (tắt, trả về giá trị gốc).

Chú thích: 🔌 = ngắt mạng vài giây khi áp dụng · 🔁 = cần khởi động lại máy · 🛡 = cần quyền Admin

Cơ chế chung (sao lưu trước, kiểm chứng, hoàn tác, khôi phục khi không có bản sao lưu): xem [ADR-0003](adr/0003-tweak-framework.md). Riêng nhóm powercfg **không** tắt được nếu không có bản sao lưu, vì không có giá trị mặc định đã biết cho từng chỉ số.

## 1. Card Wi‑Fi (thuộc tính nâng cao của driver)

Áp dụng qua `Set-NetAdapterAdvancedProperty`; khôi phục không có backup bằng `Reset-NetAdapterAdvancedProperty`. Tên thuộc tính khác nhau theo hãng chip → khớp bằng `DisplayName` (regex), tweak không có thuộc tính tương ứng sẽ hiển thị "không hỗ trợ".

| ID | Tên | Giá trị đích | Rủi ro | Ghi chú |
|---|---|---|---|---|
| `wifi_power_saving` | Tắt tiết kiệm điện của card | `Power Saving` = Disabled (MediaTek) · `MIMO Power Save Mode` = No SMPS (Intel/Realtek) | low | 🔌🛡 Nguyên nhân phổ biến gây rớt mạng ngắt quãng |
| `wifi_wake_magic` | Tắt Wake on Magic Packet | Disabled | low | 🔌🛡 Tránh card bị đánh thức/treo khi sleep |
| `wifi_wake_pattern` | Tắt Wake on Pattern Match | Disabled | low | 🔌🛡 |
| `wifi_roaming` | Giảm roaming | `Roaming Aggressiveness` = Lowest (Intel) | low | 🔌🛡 Hạn chế nhảy AP khi dùng mesh |
| `wifi_bw20_5g` | Băng thông 5GHz chỉ 20MHz | `5GHz channel bandwidth` = 20MHz only | experimental | 🔌🛡 Giảm tốc độ tối đa, đổi lấy ổn định nơi nhiễu |
| `wifi_mode_ac` | Ép chuẩn Wi‑Fi 5 (802.11ac) | `802.11ax/ac/n/abg` = 802.11ac | experimental | 🔌🛡 Thử khi nghi lỗi tương thích Wi‑Fi 6 giữa card và router |

## 2. Nguồn điện Windows

| ID | Tên | Giá trị đích | Rủi ro | Ghi chú |
|---|---|---|---|---|
| `device_power_off` | Không cho Windows tắt card | Registry `PnPCapabilities` \|= `0x18` tại class key của card | low | 🔌🛡 Tương đương bỏ chọn "Allow the computer to turn off this device…" |
| `power_wireless_max` | Wireless Adapter: Maximum Performance | `powercfg` sub `19cbb8fa-…` / setting `12bbebe6-…` = 0 (AC & DC) | low | 🛡 Chỉ áp dụng cho power plan hiện tại |
| `power_pcie_aspm_off` | Tắt PCIe ASPM | `powercfg` sub `501a4d13-…` / setting `ee12f906-…` = 0 (AC & DC) | low | 🛡 Quan trọng với card PCIe (MediaTek MT7921/7922). Tăng nhẹ điện năng |

## 3. Ngăn xếp mạng

| ID | Tên | Giá trị đích | Rủi ro | Ghi chú |
|---|---|---|---|---|
| `tcp_timedwait` | Rút ngắn TIME_WAIT | `HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\TcpTimedWaitDelay` = 30 | medium | 🔁🛡 Giảm lỗi hết port (Tcpip event 4227). Gốc không tồn tại ⇒ khôi phục bằng cách xóa giá trị |
| `ipv6_off` | Tắt IPv6 trên Wi‑Fi | `Disable-NetAdapterBinding -ComponentID ms_tcpip6` | experimental | 🔌🛡 Chỉ thử khi nghi IPv6/DNS IPv6 của router gây chậm |

## 4. Tính năng của tool

| ID | Tên | Cơ chế | Ghi chú |
|---|---|---|---|
| `watchdog` | Watchdog tự khôi phục | `settings.json` | Xem [WATCHDOG.md](WATCHDOG.md). Thao tác khôi phục cần 🛡 |
| `failover` | Chuyển sang đường mạng dự phòng ([ADR-0008](adr/0008-failover.md)) | `settings.json` → `failover` (mặc định tắt). Khi chuyển: `Set-NetIPInterface -InterfaceIndex <dự phòng> -AddressFamily IPv4 -InterfaceMetric <thấp hơn đường chính>`; sao lưu `AutomaticMetric` + `InterfaceMetric` gốc vào `backup.json` khóa `failover:<ifIndex>` trước khi ghi, đọc lại để kiểm chứng. Khôi phục (`-AutomaticMetric Enabled` hoặc metric gốc) khi đường chính ổn định 120s, khi tắt tính năng, khi monitor khởi động thấy bản sao lưu còn sót, khi gỡ cài đặt | 🛡 Không Admin ⇒ chỉ báo, chuyển khi người dùng bấm (UAC) |
| `autostart` | Khởi động cùng Windows | Task Scheduler, tạo từ XML (không dùng `schtasks /SC ONLOGON` vì mặc định của nó dừng task sau 72 giờ và không chạy khi dùng pin): trigger đăng nhập (trễ 20s), không giới hạn thời gian chạy, chạy cả khi dùng pin, tự khởi động lại tối đa 3 lần nếu crash, chạy ẩn bằng `pythonw.exe` qua `scripts/monitor/run_monitor.pyw`, không mở trình duyệt. Mặc định `/RL LIMITED` vì monitor chỉ đọc; dùng `--highest` (`/RL HIGHEST`) khi watchdog/tweak cần quyền Admin. Gỡ: `python -m app.autostart uninstall --apply` | 🛡 chỉ khi `--highest` |

## Ứng viên chưa đưa vào (cần đánh giá)

- Đổi DNS server theo kết quả benchmark (tạm thời chỉ hiển thị trong Chẩn đoán).
- Giới hạn băng thông Delivery Optimization / Windows Update chạy nền.
- Tắt QoS Packet Scheduler, Network Throttling Index — hiệu quả không rõ ràng, dễ thành "tweak placebo".
- Đổi driver (rollback) ngay trong tool — rủi ro cao, hiện chỉ hướng dẫn qua Device Manager.

## Giá trị trên máy phát triển (2026-10-03)

| ID | Hiện tại | Đề xuất |
|---|---|---|
| `wifi_power_saving` | Auto | Bật tweak |
| `wifi_wake_magic` / `wifi_wake_pattern` | Enabled | Bật tweak |
| `wifi_roaming` | — (card không hỗ trợ) | — |
| `device_power_off` | `PnPCapabilities` = 16 (cho phép tắt) | Bật tweak |
| `power_wireless_max` | AC 0 / DC 2 | Bật tweak (máy bàn, ảnh hưởng nhỏ) |
| `power_pcie_aspm_off` | AC **1 (Moderate)** / DC 2 | Bật tweak — ưu tiên cao |
| `tcp_timedwait` | không đặt (mặc định 120s) | Tùy chọn — đã có 1 sự kiện 4227 |

**Đọc lại bằng tool ngày 2026-10-04** (`python -m app.tweaks list`, chỉ đọc): chỉ `wifi_power_saving` đang bật; mọi mục còn lại ở giá trị gốc (Wake on = Enabled, `PnPCapabilities` = 16, ASPM AC 1 / DC 2, wireless AC 0 / DC 2); `wifi_roaming` không hỗ trợ (card không có thuộc tính). Xem EXPERIMENT-LOG, EXP-001.

Bật/tắt từ dòng lệnh (mặc định chỉ in kế hoạch, cần Admin khi `--apply`):

```powershell
python -m app.tweaks list
python -m app.tweaks enable power_pcie_aspm_off            # xem kế hoạch
python -m app.tweaks enable power_pcie_aspm_off --apply    # thực hiện (PowerShell chạy Admin)
python -m app.tweaks disable power_pcie_aspm_off --apply   # khôi phục từ data/backup.json
```
