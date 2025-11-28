# Python aircraft monitor

Uses the Flight Finder API to monitor for nearby flights matching an aircraft type, and then alerting if they match particular thresholds: 

https://github.com/grega/python-aircraft-monitor

For example, I quite often have low-flying A400 (~300m) passing behind the house and want to be alerted of a likely pass with enough time to grab a camera and long lens:

```
$ python aircraft_monitor.py
Monitoring for A400s within 100km of (xx.xxxx, xx.xxxx)...
Fetching flights from: http://0.0.0.0:7478/flights-in-radius?lat=xx.xxxx&lon=xx.xxxx&radius=100
Response status: 200
Found 23 flights in radius.

--- 2 A400(s) detected ---
🚨 LOW ALTITUDE ALERT: A400 ASLAN78 at 267m, 127.7km away, inf minutes until closest approach.
Alert thresholds not met. Skipping email.
ℹ️ A400 detected: FLTOT49 at 1219m, 43.3km away, 6 minutes until closest approach.
Alert thresholds not met. Skipping email.
---
```
