import subprocess,time,urllib.request,sys,os
from pathlib import Path
from playwright.sync_api import sync_playwright
proc=subprocess.Popen([sys.executable,str(Path(__file__).with_name('browser_server.py'))])
try:
 for _ in range(60):
  try:
   urllib.request.urlopen('http://127.0.0.1:5055/',timeout=1).close();break
  except Exception: time.sleep(.25)
 else: raise RuntimeError('Server unavailable')
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_BIN','/usr/bin/chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  context=browser.new_context(viewport={'width':1280,'height':900})
  errors=[]
  page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto('http://127.0.0.1:5055/admin')
  page.fill('#password','browser-test-only-123');page.click('#loginForm button')
  page.wait_for_selector('#adminPanel',state='visible')
  page.fill('input[name=photo_count]','2');page.fill('input[name=countdown]','1');page.click('#settingsForm button')
  page.wait_for_function("() => document.querySelector('#notice').textContent.includes('Đã lưu')")
  page.click('#start');page.click('#near');page.wait_for_function("() => document.querySelector('#adminState').textContent==='Sẵn sàng'")
  page.screenshot(path='/tmp/booth-admin.png',full_page=True)
  client=context.new_page();client.on('pageerror',lambda e:errors.append(str(e)))
  client.goto('http://127.0.0.1:5055/');client.click('#claim')
  client.wait_for_function("() => document.querySelector('#preview').naturalWidth>0 && !document.querySelector('#preview').hidden")
  client.select_option('#filter','hat');client.select_option('#frame','orange_photo.png')
  client.click('#capture')
  client.wait_for_function("() => document.querySelectorAll('.photo').length===3",timeout=25000)
  client.screenshot(path='/tmp/booth-client.png',full_page=True)
  client.set_viewport_size({'width':390,'height':844})
  client.screenshot(path='/tmp/booth-mobile.png',full_page=True)
  assert client.evaluate('document.documentElement.scrollWidth<=window.innerWidth'), 'Mobile overflow'
  other=browser.new_context();op=other.new_page();op.goto('http://127.0.0.1:5055/')
  op.wait_for_function("() => document.querySelector('#placeholderTitle').textContent==='Vui lòng chờ lượt'")
  page.click('#refreshSessions');page.wait_for_function("() => document.querySelectorAll('#adminSessions .photo').length===3")
  assert not errors, errors
  print('PASS: admin login/settings/start, proximity, camera preview, filter/frame selection, capture, gallery, second client lock, mobile width. JS errors:',errors)
  browser.close()
finally:
 proc.terminate()
 try:proc.wait(timeout=10)
 except subprocess.TimeoutExpired:proc.kill()
