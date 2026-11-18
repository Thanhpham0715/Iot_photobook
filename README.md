# Pi Studio — Raspberry Photobooth

Photobooth chạy cục bộ trên Raspberry Pi 4. Điện thoại/laptop mở web để xem webcam trên Pi, chọn hiệu ứng, chụp và tải ảnh. Không cần HDMI, máy in, VPS hoặc mobile app.

## Những gì đã có

- Web tiếng Việt thích ứng điện thoại và desktop, không tải thư viện từ CDN.
- Trang `/admin`: đăng nhập, Start/Stop, xem cảm biến/LED/bộ nhớ, cấu hình số ảnh, đếm ngược, ngưỡng khoảng cách, thời gian vắng và lưu ảnh.
- Quản lý các phiên chụp: xem, tải, xóa ảnh; bật/tắt filter; chọn filter mặc định; nhật ký 500 sự kiện gần nhất.
- Nút GPIO17 dùng kiểu nhấn chuyển trạng thái Start/Stop, đồng bộ với admin web.
- Siêu âm GPIO23/24: 3 mẫu liên tiếp dưới ngưỡng mới bật LED GPIO18 và camera.
- Vắng liên tục quá thời gian cài đặt: tắt LED, ngừng camera, về chờ. Nếu đang chụp thì hoàn tất chuỗi trước.
- Một trình duyệt giữ quyền điều khiển tại một thời điểm. Quyền hết hạn sau 120 giây không có polling; người khác có thể nhận lượt. Khi đang chụp không chuyển lượt.
- Filter kính/mũ theo bounding box khuôn mặt OpenCV Haar Cascade; đen trắng và sepia. **Chưa có Face Mesh và xoay filter theo góc mặt.**
- Mặc định 4 ảnh có đếm ngược, lưu JPG và dải ảnh ghép có khung tùy chọn.
- Ảnh lưu theo quyền phiên trình duyệt; admin xem tất cả. Đóng/xóa cookie hoặc đổi trình duyệt có thể mất quyền truy cập ảnh cũ, nên tải ảnh ngay.
- Tự dọn ảnh quá hạn mỗi giờ; phiên bị ngắt do khởi động lại được đánh dấu `interrupted`.
- Camera và cảm biến mô phỏng để demo khi chưa có phần cứng.

## Cài trên Raspberry Pi OS

Cần Python 3.10 trở lên. Dùng OpenCV, NumPy, Pillow và gpiozero của Raspberry Pi OS để tránh phải biên dịch trên ARM:

```bash
sudo apt update
sudo apt install -y python3-venv python3-opencv opencv-data python3-numpy python3-pil python3-gpiozero python3-lgpio
cd /home/pi/cuoiky
./scripts/install.sh
.venv/bin/python manage.py init
```

`init` yêu cầu mật khẩu admin tối thiểu 10 ký tự, lưu hash và khóa phiên trong `instance/credentials.json` (quyền 0600). Không có mật khẩu mặc định. Có thể dùng biến `BOOTH_ADMIN_PASSWORD` cho khởi tạo tự động; không đưa mật khẩu vào repo.

Máy Linux khác có thể tạo venv rồi `pip install -r requirements.txt`; chế độ GPIO cần thêm `requirements-pi.txt`. Mô hình OpenCV đã có sẵn trong `assets/models`.

## Chạy

### Demo không có webcam/cảm biến

```bash
.venv/bin/python manage.py run --demo
```

### Webcam thật, cảm biến/LED mô phỏng

```bash
.venv/bin/python manage.py run
```

### Đủ phần cứng

```bash
.venv/bin/python manage.py run --hardware
```

Đổi webcam bằng `--camera 1`. Đổi cổng bằng `--port 5001`. Thư mục dữ liệu khác: `--data /path/to/data` cho cả `init` và `run`.

Dùng `hostname -I` để tìm IP Pi. Điện thoại và Pi phải cùng mạng, router không bật client isolation:

- Chụp ảnh: `http://<IP-Pi>:5000/`
- Admin: `http://<IP-Pi>:5000/admin`

Không cần Internet sau khi cài đặt. Không gọi camera điện thoại và không cần cấp quyền camera cho trình duyệt. Server dùng Waitress; preview là JPEG dùng chung cập nhật bằng HTTP khoảng 5–6 lần/giây; trạng thái được polling 800 ms thay vì Socket.IO để giảm phụ thuộc. Chất lượng thực tế cần đo với webcam và filter trên Pi.

## Luồng demo

1. Mở `/admin`, đăng nhập rồi nhấn **Start hệ thống**.
2. Nếu chưa bật `--hardware`, nhấn **Có người · 0,6 m** trong admin.
3. Mở `/` trên điện thoại hoặc tab khác; nhấn **Bắt đầu sử dụng**.
4. Xem camera, chọn kính/mũ/màu, chọn khung cho ảnh ghép.
5. Nhấn **Chụp ảnh**. Đếm ngược được Pi quyết định, lặp theo số ảnh đã cấu hình.
6. Tải từng ảnh hoặc dải ảnh từ mục **Ảnh vừa chụp**.
7. Nhấn **Nhường lượt** nếu có người tiếp theo. Admin có thể quản lý và xóa phiên.
8. Nhấn **Rời đi · 2,5 m** để thử vắng người; sau 30 giây mặc định LED và camera tắt. Nhấn Stop dừng booth ngay cả trong lúc chụp, giữ các ảnh đã lưu và đánh dấu phiên bị ngắt.

Cảm biến không tự mở trình duyệt điện thoại. Người dùng cần mở trang trước. Cảm biến siêu âm phát hiện vật thể ở gần, không xác minh đó là con người; bộ nhận diện mặt dùng riêng để ghép filter.

## Đấu dây (số GPIO theo BCM)

Tắt nguồn trước khi đấu dây.

| Thiết bị | Kết nối |
|---|---|
| HC-SR04 VCC | 5 V, chân vật lý 2 |
| HC-SR04 GND | GND, chân 6 |
| HC-SR04 TRIG | GPIO23, chân 16 |
| HC-SR04 ECHO | Qua chia áp tới GPIO24, chân 18 |
| Nút nhấn | Một chân GPIO17 (chân 11), chân còn lại GND; chương trình dùng pull-up nội bộ |
| LED đơn | GPIO18 (chân 12) → điện trở 330 Ω → anode LED; cathode → GND |
| Webcam | USB trên Pi |

**ECHO HC-SR04 là 5 V, không nối thẳng GPIO.** Dùng ECHO → 1 kΩ → điểm nối GPIO24 → 2 kΩ → GND, tạo mức khoảng 3,33 V. Không nối nút Start vào 5 V hoặc 3,3 V theo sơ đồ báo cáo cũ: bản code này dùng nút nối GND và pull-up.

GPIO18 chỉ điều khiển LED báo nhỏ. Đèn chiếu sáng chụp ảnh cần nguồn và MOSFET/driver riêng. Siêu âm phải đặt tránh vật cản cố định trước cảm biến. Tài khoản chạy dịch vụ cần quyền truy cập `video`, `gpio` của Raspberry Pi OS.

Nguồn tham khảo đấu siêu âm: https://gpiozero.readthedocs.io/en/stable/api_input.html#distancesensor-hc-sr04

## Tự chạy cùng Pi

Sau khi đã khởi tạo admin và thử phần cứng thành công:

```bash
sudo cp deploy/photobooth.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now photobooth
sudo systemctl status photobooth
journalctl -u photobooth -f
```

Unit mẫu dùng user `pi`, đường dẫn `/home/pi/cuoiky` và `--hardware`; sửa unit nếu máy khác. Không chạy đồng thời lệnh `manage.py run` và service: chỉ một tiến trình sở hữu camera/GPIO. Dịch vụ tự chạy nhưng booth vẫn **Đã dừng**, admin/nút vật lý cần Start để kích hoạt.

Không rút nguồn bằng nút Start/Stop. Muốn tắt Pi: dừng booth rồi tắt OS bằng cách thông thường.

## Thêm tài nguyên

- `assets/filters/*.png`: PNG nền trong suốt.
- `assets/filters/filters.json`: `label`, `file`, `width` (tỷ lệ so với bề rộng mặt), `anchor_y` (vị trí tâm filter theo chiều cao mặt; 0 là trán, 1 là cằm). Chỉnh để khớp từng PNG.
- `assets/frames/*.png`: tự xuất hiện trên web; vùng trong suốt là vùng hiển thị ảnh. Khung hiện tại co giãn theo dải ảnh; nên chuẩn bị khung đúng tỷ lệ dải để tránh méo. Khung không áp dụng lên preview/từng ảnh riêng.
- Sau khi thêm filter cần khởi động lại dịch vụ và bật filter trong admin. Danh sách filter tùy chỉnh được lấy từ file cấu hình.
- Nguồn khung/mô hình ghi trong các file `SOURCES.md`. Hai ảnh filter ban đầu do người dùng cung cấp.

## Cấu trúc

```text
manage.py                 CLI khởi tạo/chạy
photobooth/__init__.py     API, đăng nhập, phân quyền, CSRF
photobooth/engine.py       Trạng thái, phiên chụp, worker duy nhất
photobooth/imaging.py      Camera, nhận diện, filter, ảnh ghép
photobooth/hardware.py     Siêu âm, nút, LED, mô phỏng
photobooth/storage.py      SQLite, ảnh, nhật ký
static/                   CSS/JS cục bộ
templates/                Web chụp và admin
assets/                   Filter, khung, mô hình
instance/                 Dữ liệu runtime (không commit)
tests/                    Kiểm thử hệ thống bằng camera mô phỏng
deploy/                   Unit systemd mẫu
```

## Kiểm thử

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Kiểm thử luồng chụp/ảnh ghép, quyền ảnh/preview, khóa phiên, CSRF/admin, cấu hình, vắng người, Stop giữa phiên, phục hồi và xóa ảnh hết hạn. Kiểm thử mô phỏng không thay thế nghiệm thu phần cứng.

## Phạm vi vận hành

Hệ thống dành cho mạng LAN tin cậy, không mở cổng lên Internet. HTTP LAN chưa mã hóa mật khẩu/ảnh trên đường truyền; nếu triển khai mạng công cộng cần HTTPS và gia cố thêm. Admin được bảo vệ bằng hash mật khẩu, CSRF, cookie HttpOnly/SameSite và giới hạn thử đăng nhập. Ảnh có quyền theo cookie trình duyệt; không có QR chuyển ảnh sang thiết bị khác.

Bản hiện tại dùng detector OpenCV thay MediaPipe để chạy với Python/OpenCV có sẵn trên Pi, không nhận diện danh tính. Chưa hỗ trợ in, Face Mesh, tự phát Wi-Fi hoặc shutdown OS từ web. Không cần các tính năng này để chạy luồng đã thống nhất.

Kiểm thử trình duyệt tùy chọn (cần Chromium và `pip install playwright` trong venv):

```bash
.venv/bin/python tests/browser_smoke.py
```

Bài test tự chạy server demo ở loopback cổng 5055, tạo dữ liệu/tài khoản tạm, kiểm tra admin → Start → mô phỏng người → chọn filter/khung → chụp → xem ảnh, khóa lượt cho trình duyệt thứ hai và kích thước mobile 390 px. Ảnh kiểm tra ghi `/tmp/booth-admin.png`, `/tmp/booth-client.png`, `/tmp/booth-mobile.png`. Có thể đặt `CHROMIUM_BIN` nếu Chromium nằm ở đường dẫn khác.


## Chưa có HC-SR04: bỏ qua cảm biến

```bash
# Webcam thật, không cần cảm biến/GPIO
.venv/bin/python manage.py run --skip-sensor

# Webcam, LED và nút thật, không khởi tạo HC-SR04
.venv/bin/python manage.py run --hardware --skip-sensor

# Camera mô phỏng, bỏ qua thao tác mô phỏng khoảng cách
.venv/bin/python manage.py run --demo --skip-sensor
```

Nhấn Start trên admin (hoặc nút vật lý nếu dùng `--hardware`) → bật LED và chuyển Sẵn sàng ngay. Camera bắt đầu lấy hình ở vòng xử lý kế tiếp. Không cần nhấn Có người, không tự nghỉ sau 30 giây; dùng Stop để tắt booth. Chế độ `--hardware --skip-sensor` vẫn yêu cầu LED/nút và GPIO hoạt động; chỉ bỏ HC-SR04. Bỏ cờ để khôi phục luồng cảm biến cũ.


Nếu GPIO18 bị kernel chiếm (ví dụ chức năng PCM_CLK), chọn một GPIO trống bằng `--led-pin` (số BCM). Trên Pi đã kiểm tra, GPIO27 đang trống:

```bash
.venv/bin/python manage.py run --hardware --skip-sensor --led-pin 27
```

Tắt nguồn rồi chuyển dây LED từ chân vật lý 12 sang chân vật lý 13 (GPIO27); giữ điện trở 330 Ω và GND. Không tắt driver của hệ điều hành để giải phóng chân nếu chưa xác định tác dụng của driver. Dùng `gpioinfo` và `pinctrl get 18,27` để kiểm tra chân.

### Điều khiển bằng cử chỉ (không dùng YOLO)

Camera thật mặc định bật MediaPipe Gesture Recognizer; chế độ demo không chạy AI.
Môi trường `.venv` cũ dùng Python 3.13 ARM64 không cài được MediaPipe 0.10.x.
Dùng môi trường riêng `.venv-gestures` với Python 3.11, giữ nguyên Python hệ thống:

```bash
cd /home/pi/cuoiky
python3.11 -m venv .venv-gestures
.venv-gestures/bin/python -m pip install -r requirements-gestures.txt -r requirements-pi.txt
```

Model đã có ở `assets/models/gesture_recognizer.task`. Nếu triển khai máy mới:

```bash
curl -fL https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task -o assets/models/gesture_recognizer.task
```

Chạy camera thật không dùng GPIO/cảm biến:

```bash
.venv-gestures/bin/python manage.py run --skip-sensor
```

Chạy cùng nút và LED GPIO27 theo cấu hình thử nghiệm hiện tại:

```bash
.venv-gestures/bin/python manage.py run --hardware --skip-sensor --led-pin 27
```

Nếu terminal cũ còn chạy booth bằng `.venv/bin/python`, nhấn Ctrl+C ở terminal đó
trước khi chạy lệnh mới để giải phóng camera và cổng 5000.
Không thêm `--demo` khi thử cử chỉ vì chế độ đó dùng ảnh mô phỏng.
Nếu chạy qua systemd, `ExecStart` phải trỏ tới `.venv-gestures/bin/python`.

Sau khi Start, mở trang khách và nhấn **Bắt đầu sử dụng** để gắn ảnh với phiên riêng
của trình duyệt. Đứng ở giữa khung hình, giữ cử chỉ vài khung hình:

- **Open Palm**: kích hoạt điều khiển cử chỉ cho phiên đang nhận quyền.
- **Thumb Up**: bắt đầu chụp theo số ảnh và thời gian đếm ngược đã cấu hình.
- **Victory / Pointing Up**: hiệu ứng tiếp theo / trước đó trong danh sách được bật;
  mỗi lần chọn một hiệu ứng, thay thế tổ hợp hiệu ứng hiện tại.
- **Closed Fist**: hủy phần còn lại của phiên đang chụp; ảnh đã chụp vẫn được giữ.
- Co ba ngón giữa/áp út/út, mở hoặc khép ngón cái–trỏ: zoom 1–2.5x.

Dưới camera có bảng trạng thái nhận diện: cử chỉ hiện tại, độ tin cậy, số mặt/tay
và 8 lệnh gần nhất kèm kết quả. Terminal in `[GESTURE]` khi cử chỉ đã được xác nhận
sau khi giữ đủ khung hình; không in mỗi frame. Bảng cũng báo khi chưa thấy mặt/tay
hoặc không gán được tay cho người ở giữa. Log được xóa khi nhường lượt hoặc dừng.

Hệ thống dùng khuôn mặt OpenCV gần trục giữa nhất làm người điều khiển và gán tay
bằng khoảng cách tương đối với các khuôn mặt. Không có khuôn mặt hoặc tay có gán
quyền mơ hồ thì không nhận lệnh. Đây là quy tắc theo vị trí, không theo dõi danh tính;
người khác đi vào giữa có thể trở thành người điều khiển, tay che chéo vẫn có thể
bị gán nhầm. Nhận cử chỉ trên ảnh gốc, trước hiệu ứng và zoom. Zoom áp dụng cả preview,
ảnh lẻ và ảnh ghép, không vẽ thông tin AI lên ảnh lưu.

Nếu thiếu thư viện/model, web vẫn hoạt động; trường `gesture_error` trong
`/api/status` và thông báo trên trang cho biết lỗi cử chỉ. Không tải model tự động
trong worker. Cần thử camera thật trên Pi để kiểm tra tốc độ và độ chính xác.
