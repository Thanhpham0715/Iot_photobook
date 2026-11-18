import logging


class Hardware:
    def __init__(self, enabled=False, skip_sensor=False, led_pin=18):
        self.enabled=enabled
        self.skip_sensor=skip_sensor
        self.sensor=self.button=self.led=None
        self.error=None
        self.simulated_distance=2.0
        self.led_on=False
        if enabled:
            stage='khởi tạo thư viện GPIO'
            try:
                from gpiozero import LED, Button, DistanceSensor
                stage=f'LED GPIO{led_pin}'
                self.led=LED(led_pin)
                stage='nút nhấn GPIO17'
                self.button=Button(17,pull_up=True,bounce_time=.15)
                if not skip_sensor:
                    stage='HC-SR04 GPIO23/24'
                    self.sensor=DistanceSensor(echo=24,trigger=23,max_distance=4,queue_len=5,partial=True)
            except Exception as exc:
                self.error=f'GPIO chưa sẵn sàng tại {stage}: {type(exc).__name__}: {exc}'
                logging.getLogger(__name__).exception(self.error)
                self.close()

    def distance(self):
        if self.skip_sensor:
            return None
        if self.enabled:
            return self.sensor.distance*4 if self.sensor else None
        return self.simulated_distance

    def light(self, value):
        self.led_on=bool(value) and not bool(self.error)
        if self.led:
            self.led.value=int(self.led_on)

    def close(self):
        for device in (self.led,self.button,self.sensor):
            if device:
                device.close()
        self.led=self.button=self.sensor=None
        self.led_on=False
