# Reversi Bot v16 — instant opening reply và pass finisher

Project chạy đường đi dữ liệu:

`ADB screenshot -> nhận diện state/8x8 -> kiểm tra luật -> engine chọn nước -> ADB tap -> xác nhận state đổi`

Project không đọc API riêng của WePlay. Mọi action đều qua ADB, mặc định tắt và cần người dùng bật rõ ràng.

## Hai mode

- **Mode 1 — ổn định** (mặc định): pipeline cũ, xác nhận bàn 2–3 frame rồi mới tìm nước.
- **Mode 2 — phản công nhanh**: tăng tốc opening tới 22 quân. Tám Edax worker một thread xử lý các case song song; mỗi case có ngân sách riêng tối đa 3 giây. Nước tối ưu dự đoán của đối thủ được gửi trước và kết quả từng case được công bố ngay khi xong, không đợi toàn bộ bảng. Case thực tế chưa sẵn sàng thì lượt đó tự quay về engine thường.
- Khi nước vừa chọn khiến đối thủ hết nước nhưng mình còn nước, Mode 2 chuyển sang **pass-chain**: Edax endgame tính nối tiếp và công bố từng bước ngay khi xong. Board thực tế phải khớp chính xác board dự kiến; khi đó chỉ cần 1 frame xác nhận. Nếu cache chưa kịp sẵn sàng, bot dùng luồng thường.
- Hai kiểu speculation không chạy chồng logic: `đối thủ có nước -> response book`; `đối thủ không có nước -> pass-chain`. Thứ tự sử dụng là pass-cache, response-book, rồi engine thường.
- Có thể tích chọn mode khi bot đang chạy. Đổi trong ván chỉ có hiệu lực từ ván kế tiếp.
- Cả hai mode nhận diện quân đối thủ có skin màu/texture khác. Quân cổ điển của người dùng xác định phía mình; quân tròn có texture/màu còn lại được ánh xạ sang phía đối thủ rồi kiểm tra lại bằng luật và ô vàng trước khi click.

Mode 2 tự làm nóng tám worker ngay khi mở monitor và cache sẵn nước đầu nếu người dùng cầm đen. Phép đo cục bộ trên ba thế mẫu 4/5/7 nhánh hoàn tất trong khoảng 270/665/521 ms bằng Edax L22; từng case thường sẵn sàng sớm hơn tổng thời gian này. Thế pass hai bước mẫu được cache trong khoảng 39 ms. Đây là số đo trên máy thử nghiệm, không phải cam kết cho mọi thế/máy.

Engine mặc định của bản này là hybrid **Egaroucid for Console 7.8.1 + Edax 4.6 AVX2**, mỗi engine dùng 2 thread. Hai tiến trình được nạp sớm khi mở monitor; mọi nước trả về đều được đối chiếu lại với luật Reversi nội bộ trước khi click. Nếu engine ngoài thiếu, crash, trả nước sai hoặc quá hạn, bot dùng kết quả còn sống hoặc chuyển sang Python depth 5. Khung bên phải hiển thị `Engine:` để biết nhánh nào thật sự đã chọn nước.

Chính sách thời gian:

- Chỉ có một nước hợp lệ: đi ngay, không gọi AI.
- Tối đa 16 quân trên bàn: Egaroucid L6 tận dụng opening book; không cố tình delay nếu book đã biết nước.
- Midgame dễ: chạy song song Egaroucid L4 và Edax L16; nếu đồng thuận thì click sớm.
- Midgame khó: chạy song song Egaroucid L12 và Edax L28. Tổng ngân sách engine tối đa khoảng 13,2 giây trong ngân sách lượt 15 giây, chừa thời gian cho CV và ADB click.
- Khi hai engine mạnh bất đồng, mặc định Edax làm verifier. Chỉ đổi sang nước Egaroucid để ép mobility nếu nó giảm ít nhất 2 nước đáp trả và không tặng góc cho đối thủ.
- Còn dưới 18 ô trống: `endgame rush`, chạy Egaroucid L9 và Edax L60 song song với trần 4,5 / 3,0 / 1,5 giây khi số ô trống giảm dần.
- Mode 1 lấy ảnh mỗi 0,2 giây; Mode 2 yêu cầu frame mới ngay khi lần chụp trước xong. ADB serial được cache thay vì dò lại ở mọi frame. Trên máy thử nghiệm, PNG sau warm-up ~209 ms/frame còn raw RGBA ~389 ms/frame, nên bot giữ PNG vì nhanh hơn thực tế.
- Mode 2 dùng fast-path ngay ở frame đầu tiên khi board mới khớp chính xác một case đã hoàn thành; không bắt buộc chờ ô vàng. Nếu tap sớm hơn lúc WePlay nhận input, bot chỉ retry đúng một lần khi ô vàng xuất hiện. Case chưa sẵn sàng hoặc board không khớp vẫn dùng luồng Mode 1.
- Quyết định được cache theo thế cờ, nên bot không gọi lại engine trong lúc chờ animation/xác nhận click.

## Test monitor WePlay trên LDPlayer

Bản monitor đọc realtime từ LDPlayer 14 bằng ADB. Vòng lặp tự động mặc định tắt và chỉ được bật bằng checkbox có xác nhận.

Lần đầu:

```powershell
.\setup.ps1
```

Mỗi lần chạy:

```powershell
cd "Reversi_yalpew_tools"
Set-ExecutionPolicy -Scope Process Bypass
.\run_monitor.ps1
```

Trong cửa sổ monitor:

1. Giữ **Tự động** (khuyến nghị). Monitor suy ra màu từ bàn và các ô vàng; Đen/Trắng chỉ là override để debug.
2. Chọn **Mode 1 — ổn định** hoặc **Mode 2 — phản công nhanh**. Mặc định là Mode 1.
3. Bấm **Test ảnh mẫu** để kiểm tra pipeline không cần mở LDPlayer.
4. Mở LDPlayer và WePlay, sau đó bấm **Theo dõi LDPlayer**.
5. Hộp thoại hỏi chế độ sẽ hiện ngay: chọn **Yes** để bật AUTO LOOP; chọn **No** chỉ chạy DRY RUN và bot tuyệt đối không click.
6. Bot thực hiện: sảnh -> bấm **Trận sơ cấp** -> màn ghép chỉ chờ -> màn tải chỉ chờ -> tự đánh -> bấm **Chơi lại** -> lặp lại. Nếu đối thủ rời ngay sau khi ghép, bot nhận popup và bấm **Thoát** một lần để quay về sảnh.
7. Bấm **Dừng** sẽ tắt monitor lẫn auto-loop.

Luôn kiểm tra góc phải cửa sổ: phải hiện `AUTO LOOP: ON` nếu muốn bot đánh. Bản này hiển thị `DRY RUN — KHÔNG CLICK` và ghi rõ lý do trong log ở cả nước cờ lẫn màn Chơi lại, tránh hiểu nhầm monitor đang điều khiển game.

Trong một ván, vị trí bàn được khóa sau hai lần phát hiện hình học liên tiếp. Frame nhiễu không được phép kéo khung click sang vị trí mới. Khi sang sảnh, ghép trận hoặc kết quả, khóa được xóa để nhận layout mới.

Màn tải nền sáng được nhận thành state `LOADING` trước khi chạy grid detector. State này không có board, không có action và xóa grid lock từ màn trước.

Màn kết quả hỗ trợ cả hai giao diện: banner/nút cam của `WIN` và banner/nút xám của `LOSE`. Với `LOSE`, recognizer yêu cầu đồng thời layout bàn kết quả, ít nhất 50 quân và nút xám rộng bên dưới bàn để tránh nhầm một lượt chờ cuối game thành kết quả.

Cấu hình đã phát hiện trên máy thử nghiệm: `C:\LDPlayer\LDPlayer14`, Android `540x960`. Monitor tự chọn instance đang chạy; chỉ truyền `--index` khi mở nhiều instance cùng lúc.

## Chạy ngay trên Windows

Không cần cài package cho demo PPM:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\run_demo.ps1
```

Hoặc chạy từng lệnh:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m reversi_bot.demo examples\initial_board.ppm
python -m reversi_bot examples\initial_board.ppm --config config\sample_board.json --player black
python -m unittest discover -s tests -v
```

Để đọc screenshot PNG/JPEG thật:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[images]"
reversi-bot path\to\screenshot.png --config config\weplay_local.json --player black
```

Output gồm bàn cờ (`B`, `W`, `.`, `*` là nước hợp lệ), nước chọn theo ký hiệu như `d3`, tọa độ pixel, độ sâu tìm kiếm, số node và confidence thấp nhất.

## Kiến trúc

| Module | Hiện tại | Giao kèo mở rộng |
|---|---|---|
| State acquisition | ADB screenshot realtime và ảnh fixture | hỗ trợ nhiều emulator/instance |
| Board recognition | tự tìm grid, đọc 64 ô/highlight và khóa grid theo ván | thêm perspective warp, calibration theo theme |
| Reversi engine | Egaroucid 7.8.1 + Edax 4.6 hybrid, mobility guard, endgame rush + Python fallback | benchmark nhiều ván thật và tune theo timer |
| Action executor | ADB tap có kiểm tra biên ảnh và đúng serial | circuit breaker tổng quát |
| State machine | lobby/matching/play/result, ổn định 3 frame, một action/state | lưu phiên và thống kê ván |
| Integration | GUI monitor + CLI/JSON | event log JSONL, replay runner |

Luồng dự kiến khi nối live:

```text
CAPTURE -> RECOGNIZE -> VALIDATE -> THINK -> (dry-run / ACT)
   ^                                            |
   +-------- WAIT <- VERIFY <- RESULT ----------+
```

Ba invariant nên giữ:

1. Không click nếu nhận diện có ô dưới ngưỡng confidence hoặc trạng thái bàn cờ bất hợp lệ.
2. Nước engine trả về phải nằm trong tập `legal_moves` tính độc lập từ board đã nhận diện.
3. Sau action phải chụp lại và xác nhận board đổi đúng một nước; nếu không thì retry có giới hạn rồi dừng.

## Hiệu chỉnh một screenshot thật

Sao chép `config/sample_board.json` thành file local rồi sửa:

- `board_rect`: `[left, top, right, bottom]`, biên phải/dưới là exclusive.
- `colors.empty|black|white`: RGB mẫu từ theme hiện tại.
- `sample_radius_ratio`: bán kính vùng median so với kích thước ô; mặc định `0.18` tránh đường kẻ.
- `max_color_distance`: khoảng cách RGB dùng để đổi thành confidence.
- `min_cell_confidence`: dưới ngưỡng này pipeline dừng thay vì đoán.

MVP giả định bàn cờ thẳng, đủ 8x8, không animation/overlay và quân nằm gần tâm ô. OCR chưa cần thiết cho chính board; OCR chỉ hữu ích về sau để đọc lượt, timer hoặc màn kết quả.

## Roadmap theo phase

### Phase 0 — contract và replay corpus

- Chốt quy ước màu quân, chiều bàn, ai đi trước và mapping ô -> pixel.
- Thu 30–50 screenshot đã gán nhãn, phủ theme, độ phân giải, animation và popup.
- KPI: board exact-match; không chỉ cell accuracy. Mục tiêu gợi ý >= 99% exact-match trên tập holdout trước live action.

### Phase 1 — MVP offline (project này)

- Ảnh tĩnh -> board -> legal moves -> best move -> pixel.
- Unit test luật và integration test ảnh tổng hợp.
- JSON output để nối module khác mà không phụ thuộc UI.

### Phase 2 — CV chịu biến thiên

- OpenCV: tìm contour/4 góc, homography về ảnh vuông chuẩn, classifier HSV/Lab.
- Phát hiện trạng thái `not_in_game`, `your_turn`, `animation`, `game_over`.
- Chạy replay toàn corpus, xuất confusion matrix và danh sách ảnh fail.

### Phase 3 — engine mạnh và đo được (đã tích hợp bước đầu)

- Dùng Egaroucid Console 7.8.1 và Edax 4.6 AVX2; binary/resources nằm trong `engines/egaroucid` và `engines/edax`.
- Hai adapter giữ tiến trình chạy xuyên suốt, áp ngân sách theo phase, kiểm tra nước hợp lệ và fallback khi lỗi.
- Vẫn cần đấu đối kháng trên chính tập ván WePlay để chọn level tối ưu; `level` của Egaroucid không đồng nghĩa với `depth`.

### Phase 4 — live capture nhưng dry-run

- Capture cửa sổ hoặc emulator; log ảnh hash, board, confidence, move, timing.
- Chỉ hiển thị overlay/gợi ý, chưa click. So khớp thủ công ít nhất vài trăm lượt.

### Phase 5 — action executor có khóa an toàn

- Windows `SendInput` cho cửa sổ cố định hoặc ADB cho Android emulator.
- Bắt buộc: focus/window identity, kill switch, rate limit, verify-after-click, không xử lý captcha/anti-bot.
- Xem lại điều khoản dịch vụ WePlay trước khi bật; account phụ không làm mất rủi ro vi phạm quy định nền tảng.

## Stack đề xuất

- Python 3.11–3.13 cho hệ sinh thái CV ổn định hơn bản 3.14 mới.
- Pillow cho I/O ảnh; OpenCV + NumPy ở Phase 2.
- `mss` hoặc Windows Graphics Capture cho desktop; ADB screencap/input cho emulator.
- `pytest`, JSONL logging, `ruff`, `mypy`; GitHub Actions nếu đưa lên repo.
- Không ưu tiên lấy private API: khó ổn định, có thể đụng xác thực/ToS. Screen-based adapter tách biệt là baseline kiểm chứng được.

## Giới hạn đã biết

- Bộ nhận diện đã được kiểm tra trên các ảnh người dùng cung cấp và live ADB 540x960, chưa bảo đảm cho theme hay độ phân giải khác.
- Frame animation được bỏ qua bằng validation và khóa grid; chưa có mô hình thị giác học máy.
- Chưa đo Elo riêng trong môi trường WePlay; benchmark của tác giả là bằng chứng tham khảo chứ không bảo đảm tỷ lệ thắng trên app.
- Nút chỉ được bấm khi nhận diện được vùng màu đặc trưng; nếu giao diện đổi, bot sẽ đứng chờ thay vì đoán tọa độ.

## Engine bên thứ ba

Egaroucid và Edax được phát hành theo GPL-3.0. Nguồn, bản phát hành, license và SHA-256 của các file ZIP nằm trong `THIRD_PARTY_NOTICES.md`. Không xóa `engines/egaroucid/resources` hoặc `engines/edax/data/eval.dat`.
