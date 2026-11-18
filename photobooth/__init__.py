import json
import os
import secrets
import time
from functools import wraps
from pathlib import Path
from flask import Flask, abort, jsonify, render_template, request, session, send_from_directory
from werkzeug.security import check_password_hash
from .engine import Booth
from .storage import Store

ROOT=Path(__file__).resolve().parent.parent


def create_app(data_dir=None, demo=False, hardware=False, camera=0, start_worker=True, skip_sensor=False, led_pin=18):
    app=Flask(__name__,template_folder=str(ROOT/'templates'),static_folder=str(ROOT/'static'))
    root=Path(data_dir or os.environ.get('BOOTH_DATA',ROOT/'instance'))
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    credentials=root/'credentials.json'
    if not credentials.exists():
        raise RuntimeError('Chưa tạo tài khoản admin. Chạy: python manage.py init')
    auth=json.loads(credentials.read_text())
    app.config.update(SECRET_KEY=auth['secret'],SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',MAX_CONTENT_LENGTH=16384)
    store=Store(root)
    booth=Booth(store,ROOT/'assets',demo,hardware,camera,skip_sensor,led_pin)
    app.extensions['booth']=booth
    app.extensions['store']=store
    attempts={}

    def admin_required(fn):
        @wraps(fn)
        def wrapper(*a,**kw):
            if not session.get('admin'): abort(403)
            return fn(*a,**kw)
        return wrapper

    @app.before_request
    def security():
        session.permanent=False
        if 'visitor' not in session: session['visitor']=secrets.token_hex(24)
        if 'csrf' not in session: session['csrf']=secrets.token_hex(24)
        if request.method in ('POST','PUT','DELETE'):
            token=request.headers.get('X-CSRF-Token','')
            if not secrets.compare_digest(token,session['csrf']): abort(403)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.path.startswith(('/api','/media')): response.headers['Cache-Control']='no-store'
        return response

    @app.errorhandler(ValueError)
    def invalid(exc): return jsonify(error=str(exc)),409

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def http_error(exc): return jsonify(error={400:'Yêu cầu không hợp lệ',403:'Không có quyền hoặc phiên đã hết hạn. Hãy tải lại trang.',404:'Không tìm thấy',413:'Dữ liệu quá lớn'}.get(exc.code,'Lỗi')),exc.code

    def body():
        data=request.get_json()
        if not isinstance(data,dict): abort(400)
        return data

    def frames():
        return [p.name for p in sorted((ROOT/'assets/frames').glob('*.png'))]

    @app.get('/')
    @app.get('/admin')
    def index(): return render_template('index.html',csrf=session['csrf'],admin_page=request.path=='/admin')

    @app.get('/api/status')
    def status():
        data=booth.status(session['visitor'],session.get('admin',False))
        cfg=store.settings()
        data.update(admin=bool(session.get('admin')),filters=[o for o in booth.camera.options() if o['id'] in cfg['enabled_filters']],frames=frames())
        if session.get('admin'):
            data['settings']=cfg
            data['all_filters']=booth.camera.options()
        return jsonify(data)

    @app.post('/api/login')
    def login():
        ip=request.remote_addr
        now=time.monotonic()
        record=attempts.setdefault(ip,[])
        record[:]=[t for t in record if now-t<300]
        if len(record)>=5: return jsonify(error='Thử lại sau 5 phút'),429
        password=body().get('password','')
        if not isinstance(password,str) or not check_password_hash(auth['password_hash'],password):
            record.append(now)
            return jsonify(error='Sai mật khẩu'),403
        attempts.pop(ip,None)
        session['admin']=True
        store.log('Admin đăng nhập')
        return jsonify(ok=True)

    @app.post('/api/logout')
    def logout():
        session.pop('admin',None)
        return jsonify(ok=True)

    @app.post('/api/control')
    @admin_required
    def control():
        action=body().get('action')
        if action=='start': booth.start()
        elif action=='stop': booth.stop()
        else: abort(400)
        return jsonify(ok=True)

    @app.post('/api/simulate')
    @admin_required
    def simulate():
        if booth.skip_sensor: raise ValueError('Đang bỏ qua cảm biến')
        if booth.hardware.enabled: raise ValueError('Đang sử dụng cảm biến thật')
        value=body().get('near')
        if type(value) is not bool: abort(400)
        booth.hardware.simulated_distance=.6 if value else 2.5
        return jsonify(ok=True)

    @app.post('/api/claim')
    def claim():
        booth.claim(session['visitor'])
        return jsonify(ok=True)

    @app.post('/api/release')
    def release():
        with booth.lock:
            booth._owner(session['visitor'])
            if booth.state=='capturing': raise ValueError('Đang chụp, vui lòng chờ')
            booth.owner=None
            booth.gestures.reset()
            booth.camera.zoom=1.0
        return jsonify(ok=True)

    @app.post('/api/select')
    def select():
        data=body()
        # Omitted fields preserve the current choice; old single-filter clients still work.
        with booth.lock:
            effects=data.get('filters',data.get('filter',booth.effects))
            if isinstance(effects,str): effects=[] if effects=='none' else [effects]
            frame=data.get('frame',booth.frame)
            enabled=store.settings()['enabled_filters']
            if (not isinstance(effects,list) or len(effects)>len(enabled)
                or not all(isinstance(e,str) and e!='none' and e in enabled for e in effects)
                or len(set(effects))!=len(effects)
                or ('mono' in effects and 'sepia' in effects)
                or not isinstance(frame,str) or frame not in ['none']+frames()): abort(400)
            booth.select(session['visitor'],effects,frame)
        return jsonify(ok=True)

    @app.post('/api/capture')
    def capture(): return jsonify(id=booth.capture(session['visitor']))

    @app.get('/api/preview.jpg')
    def preview():
        with booth.lock:
            if not session.get('admin') and (booth.owner!=session['visitor'] or (booth.lease<time.monotonic() and booth.state!='capturing')): abort(403)
            if not booth.camera.jpeg: return '',204
            return app.response_class(booth.camera.jpeg,mimetype='image/jpeg')

    @app.get('/assets/frames/<path:name>')
    def frame_asset(name):
        if name not in frames(): abort(404)
        return send_from_directory(ROOT/'assets/frames',name)

    @app.get('/api/sessions')
    def sessions(): return jsonify(store.sessions(None if session.get('admin') else session['visitor']))

    @app.get('/media/<name>')
    def media(name):
        allowed=store.sessions(None if session.get('admin') else session['visitor'])
        if not any(name in s['files'] for s in allowed): abort(404)
        return send_from_directory(store.photos,name,as_attachment=request.args.get('download')=='1')

    @app.delete('/api/sessions/<sid>')
    @admin_required
    def delete(sid):
        with booth.lock:
            if booth.state=='capturing' and booth.sid==sid: raise ValueError('Không thể xóa phiên đang chụp')
            if not store.delete(sid): abort(404)
            store.log('Admin xóa phiên '+sid[:8])
        return jsonify(ok=True)

    @app.get('/api/events')
    @admin_required
    def events(): return jsonify(store.events())

    @app.post('/api/settings')
    @admin_required
    def settings():
        data=body()
        ranges={'distance_m':(.2,3.5),'absence_seconds':(5,300),'photo_count':(1,8),'countdown':(1,10),'retention_days':(1,90)}
        if set(data)!=set(ranges)|{'enabled_filters','default_filter'}: abort(400)
        for k,(lo,hi) in ranges.items():
            v=data[k]
            if type(v) not in (int,float) or not lo<=v<=hi or (k!='distance_m' and type(v) is not int): abort(400)
        enabled=data['enabled_filters']
        known={o['id'] for o in booth.camera.options()}
        if not isinstance(enabled,list) or not all(isinstance(e,str) and e in known for e in enabled) or 'none' not in enabled or data['default_filter'] not in enabled: abort(400)
        with booth.lock:
            if booth.state=='capturing': raise ValueError('Hãy đợi phiên chụp kết thúc trước khi đổi cấu hình')
            store.configure(data)
            booth.effects=[effect for effect in booth.effects if effect in enabled]
        store.log('Admin cập nhật cấu hình')
        return jsonify(ok=True)

    if start_worker: booth.run()
    return app
