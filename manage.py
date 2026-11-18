#!/usr/bin/env python3
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
from werkzeug.security import generate_password_hash


def main():
    parser=argparse.ArgumentParser(description='Raspberry Photobooth')
    parser.add_argument('command',choices=['init','run'])
    parser.add_argument('--data',default=os.environ.get('BOOTH_DATA',str(Path(__file__).parent/'instance')))
    parser.add_argument('--demo',action='store_true',help='Camera mô phỏng, không phải camera thật')
    parser.add_argument('--hardware',action='store_true',help='Sử dụng GPIO thật')
    parser.add_argument('--skip-sensor',action='store_true',help='Bỏ qua HC-SR04; sẵn sàng ngay sau Start, không tự nghỉ khi vắng người')
    parser.add_argument('--led-pin',type=int,choices=range(2,28),default=18,help='Chân BCM cho LED, mặc định 18')
    parser.add_argument('--camera',type=int,default=0)
    parser.add_argument('--host',default='0.0.0.0')
    parser.add_argument('--port',type=int,default=5000)
    args=parser.parse_args()
    if args.hardware and (args.led_pin==17 or (not args.skip_sensor and args.led_pin in (23,24))):
        parser.error('Chân LED trùng nút hoặc cảm biến')
    if args.command=='init':
        root=Path(args.data)
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=root/'credentials.json'
        if path.exists():
            parser.error('Tài khoản đã tồn tại. Để đặt lại mật khẩu, sao lưu rồi xóa credentials.json; mọi phiên đăng nhập sẽ hết hiệu lực.')
        password=os.environ.get('BOOTH_ADMIN_PASSWORD') or getpass.getpass('Mật khẩu admin (ít nhất 10 ký tự): ')
        if len(password)<10: parser.error('Mật khẩu cần ít nhất 10 ký tự')
        with path.open('x') as f:
            os.chmod(path,0o600)
            json.dump(dict(secret=secrets.token_hex(32),password_hash=generate_password_hash(password)),f)
        print('Đã tạo tài khoản admin tại',root)
        return
    from photobooth import create_app
    from waitress import serve
    app=create_app(args.data,args.demo,args.hardware,args.camera,skip_sensor=args.skip_sensor,led_pin=args.led_pin)
    print(f'Photobooth: http://<IP-Pi>:{args.port} | Admin: /admin',flush=True)
    try: serve(app,host=args.host,port=args.port,threads=8)
    finally: app.extensions['booth'].close()


if __name__=='__main__': main()
