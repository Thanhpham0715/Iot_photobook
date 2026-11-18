# Face detector

haarcascade_frontalface_default.xml: bản gốc từ OpenCV 4.10.0.
https://github.com/opencv/opencv/blob/4.10.0/data/haarcascades/haarcascade_frontalface_default.xml

Giữ nguyên phần thông tin giấy phép Intel trong đầu file XML.
Không cần mạng để nhận diện khi chạy hệ thống.

# Eye detector

haarcascade_eye.xml: bản gốc từ OpenCV 4.10.0.
https://github.com/opencv/opencv/blob/4.10.0/data/haarcascades/haarcascade_eye.xml
Dùng để xác định góc nghiêng mặt và xoay filter không cắt góc.

# Gesture recognizer

gesture_recognizer.task: MediaPipe Gesture Recognizer, float16, version 1.
https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task

Nhận cử chỉ chạy cục bộ bằng MediaPipe Tasks; không dùng YOLO/Ultralytics.
