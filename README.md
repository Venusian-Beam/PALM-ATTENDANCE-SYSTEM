# PAS: Face Attendance (multiple kiosks, one school server)

Students clock in and out by looking at a kiosk camera and blinking. Every kiosk records to one database on the school server. Only the IT officer can register students and approve devices.

```
 Kiosk laptops (camera)  ──HTTPS──►  School server PC  ◄──HTTPS──  IT officer's browser
 python main.py kiosk                python main.py server         https://<server-ip>:8443/admin
 local copy: data/kiosk.db           database: data/pas.db (SQLite)
```

## What you need

| Item | Notes |
|---|---|
| **1 server PC** (Windows) | Must stay switched on during school hours. It holds the database (`data/pas.db`, SQLite) and the admin portal. It doesn't need a camera. |
| **1 or more kiosk laptops** (Windows) | Each needs a webcam (an external 1080p one is best), with light in front of the students and no window behind them. |
| **Network** | All machines on the school network. Kiosks on the school Wi-Fi. |
| **Software on every machine** | Python 3.11+. |
| **Software for building the screens** | Node.js 20+, needed only once. You can copy the built `frontend/dist` folder to the other machines. |
| **Windows settings on kiosks** | Location services ON, including "Let desktop apps access your location". "Random hardware addresses" OFF for the school Wi-Fi. |

Each machine gets its own copy of the PAS folder, with its own `config.yaml`.

## 1. Install (on every machine)

```
cd PAS
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On kiosks, also download the face models:
```
python scripts\download_models.py
```

Build the screens. This needs Node.js; do it once, then copy `frontend\dist` to the other machines:
```
cd frontend
npm install
npm run build
cd ..
```

## 2. Set up the server PC

```
python scripts\configure_server.py
python main.py server
```

`configure_server.py`:
- sets the IT officer's admin password
- asks for the school Wi-Fi name and the school's location
- creates the HTTPS certificate
- prints the server address (e.g. `https://192.168.1.20:8443`) and the certificate fingerprint

When Windows Firewall asks, allow **private networks**.

**Start automatically:** in Task Scheduler, create a task that runs `C:\path\to\PAS\.venv\Scripts\python.exe main.py server`. Set it to:
- start in `C:\path\to\PAS`
- trigger "At startup"
- "Run whether user is logged on or not"

Backups are made every day in `data\backups` and kept for 30 days.

## 3. Set up each kiosk

```
python scripts\configure_kiosk.py
```
1. Enter the server address.
2. Check that the fingerprint matches the one in the admin portal (Settings), then confirm.
3. The script prints this laptop's MAC addresses.
4. In the admin portal, go to **Devices > Add device** and enter a name (e.g. "Main gate") and one of those MACs.
5. Start the kiosk:
   ```
   python main.py kiosk
   ```
   Edge opens full screen. Until the device is added, the kiosk shows "This device is not approved" and lists its MACs.

To move the data from the old single-kiosk version, run this on the server after `configure_server.py`:
`python scripts\migrate_v1.py --old-db <old data\pas.db> --old-config <old config.yaml>`

## Daily use

**Students:**

| Situation | What to do | Result |
|---|---|---|
| Arriving | Look at the camera, blink once | "Welcome back to school, \<name\>!" |
| Leaving before the clock-out time (default 17:30) | Blink **3 times** | Clocked out |
| After the clock-out time | Blink once | Clocked out |
| First scan of the day after the clock-out time | Blink once | Clocked in, noted "No morning sign-in". The next scan clocks out. |
| Coming back after clocking out | Blink, then **nod / Yes** (or shake / No) | Clocked in again |

Clocking in at one gate and out at another works, because every kiosk sees the others' records.

**IT officer:** open `https://<server-ip>:8443/admin`, or press **Ctrl+Shift+A** on a kiosk. The portal has these pages:
- **Register:** enter a name and student ID, pick a kiosk, then the student blinks and 5 photos are taken. The student is then known at every kiosk.
- **Students:** search, re-capture a face, or delete a student.
- **Attendance:** clock-in and clock-out times per day, which kiosk was used, and notes ("No morning sign-in", "No sign-out").
- **Devices:** add or remove kiosks by MAC. Shows online/offline status, records waiting to upload, and a warning if one MAC is used from two computers.
- **Settings:** clock-out time, school Wi-Fi and allowed access points, school location, password, certificate fingerprint, and the activity log.

## Bumps and shaking

If a kiosk is bumped while a student is signing in, the student doesn't have to wait or start again:
- **Skipped frames:** blurry or shaken camera frames are skipped, never counted against the student, so a shake can't cause "Face not recognised".
- **Paused timers:** the blink and question timers pause while the picture is unusable, for up to 4 seconds per check.
- **Re-check, not restart:** if the picture jumps, the student's face is checked again on the next sharp frame, and the check continues with the blinks already done. Someone else stepping in is still caught.
- **"Hold still…" hint:** shown only if shaking lasts more than about 1 second.
- **No false answers:** a turning kiosk is never read as a head shake.
- **Sharp registration photos:** registration only keeps sharp photos.

**Tips:** put the kiosk on a stable stand, or use an external webcam on a mount. Good light in front of the students also helps, because it means less blur. You can tune all of this in the kiosk `config.yaml` under `stability`.

## Bright light behind students

A window or open door behind a student makes the webcam darken the whole picture, so the face becomes a silhouette. PAS never changes the webcam's settings, and the kiosk screen shows the camera's picture as it is. The kiosk handles backlight in two ways:
- **Software:** dark faces are brightened in memory (never on screen) before the face is found, recognised and checked for blinks. If no face can be seen at all, the kiosk looks again in a brightened copy. Normally lit faces are not changed.
- **People:** if the face is still too dark after about 1.5 seconds, students see "Too much light behind you". When many of a kiosk's scans in a day are backlit, the IT officer sees a **Backlit** chip on that kiosk in **Devices** and on the dashboard.

Registration never saves a backlit face. Move the student or the kiosk first.

**Best fix:** don't point kiosks at windows or doors, and put a lamp or light in front of the students. You can tune all of this in the kiosk `config.yaml` under `lighting`.

## Offline behaviour

If a kiosk can't reach the server:
- it **keeps working** and stores clock-ins and clock-outs locally
- it uploads them automatically when the connection returns
- it locks after 12 hours without contact (`kiosk.max_offline_hours`)

Registering students needs the kiosk to be online.

## Photos, phones and screens

If someone holds up a photo, phone, tablet or laptop instead of their face, the kiosk:
- **blurs** the camera picture
- shows **"Foreign object detected"**
- records nothing

It goes back to normal one second after the object is taken away.

How it decides (each check needs strong, repeated evidence, so real students aren't blocked):
- **Live-face model.** A pre-trained anti-spoof network (MiniFASNetV2) scores every frame. It blocks only when most of the last 10 frames score as "not live".
- **Object detector.** A pre-trained detector (NanoDet, COCO) blocks when it sees a phone, laptop or screen *at the face* in at least 2 of its last 3 runs. Screens and laptops in the background are ignored.
- **Blinking.** A photo cannot blink:
  - A blink only counts when the eyes close while the rest of the face keeps its shape, so tilting a photo doesn't fake one.
  - A face that is asked to blink but never really blinks is blocked.
  - A face that really blinks is never blocked for a borderline score.
- **Supporting checks,** used only when the score is also low: the edges of a photo or phone around the face, and a face that moves like a flat sheet.

Every attempt shows on the **Devices** page as a red **Spoof attempts** chip (today's count). The **Activity log** says what was held up and whose face it looked like. No pictures are kept.

**Phone training** (Devices, tune icon, **Phones** tab):
1. Run **Phones** (30 s) while you hold phones up to your face. Vary the angle and distance, with the screen on and off.
2. Run **No phone** (30 s) with just faces in view.
3. Click **Save**. The kiosk's phone sensitivity is set where phones are caught but normal frames are not.

**Photo tuning** (same dialog, **Photos** tab):
1. Run **Real faces** (30 s) while a few people look at the camera.
2. Run **Photos & phones** (30 s) while you hold up printed photos and phone screens.
3. Click **Save**. Run it again if the kiosk is moved or the lighting changes.

Only score statistics are sent, never images.

The models are downloaded by `scripts\download_models.py` (checksums are verified).

## Security, and its limits

Attendance is recorded only when all of these are true:
- the kiosk is approved in Devices
- it's on the school Wi-Fi (and an allowed access point, if any are set)
- it's inside the school's location radius
- the student blinks
- the same single face stays in view throughout

Further protections:
- **Server checks:** the server re-checks every record's Wi-Fi and location.
- **Pinned certificate:** kiosks only accept the school server's own certificate.
- **Admin portal:** password-protected, with lockout after wrong attempts, and every admin action logged.

**Known risk (MAC-only approval, as chosen):** the server trusts the MAC address a kiosk reports. Someone on the school network who knows an approved MAC and builds their own program could send fake records. They would still need to report the school Wi-Fi and location. The Devices page warns if one MAC is used from two computers. A per-device secret key can be added later to close this gap.

Other limits:
- **Admin rights:** someone with administrator rights on a kiosk can fake its location and Wi-Fi. Run kiosks under a standard Windows account, ideally in Windows Assigned Access.
- **Very good fakes:** the photo and phone checks stop printed photos and most phone or laptop replays. A high-quality video on a large screen, or a 3D mask, may still pass occasionally. Supervise the kiosks at busy times.
- **Consent:** get parental consent before registering students. Only face measurements are stored, never photos.

## Tuning (kiosk `config.yaml`)

- **Students not recognised:** improve the lighting and re-capture their face. Don't lower `face.match_threshold` below 0.36.
- **Nod or shake not detected:** lower `gestures.nod_threshold` / `gestures.shake_threshold`. The Yes/No buttons always work.

## Tests

Run `pytest`.
