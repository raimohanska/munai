# Munai

Home Assistant app that recognizes the current nest occupant and creates MQTT
**Occupant** and **Egg count** sensors. Egg count estimates visible eggs when no
hen is detected and reports **0 when a hen is present**, including unknown hens.
[Offline egg counting](DOCS.md#offline-egg-counting) is available for calibration.

**Already installed and paired?** Continue with [upload references](#3-upload-references),
then [enable MQTT](#4-enable-mqtt) and [enable capture](#5-enable-capture).

All terminal commands below run **inside the VS Code devcontainer**, from the
repository directory. No separate CLI installation, venv, or wrapper is needed.

## 1. Install the HA app

1. In Home Assistant, open **Settings → Apps → Install app → ⋮ → Repositories**
   (older versions: **Add-ons → Add-on Store**).
2. Add this public GitHub repository's URL and install **Munai**. It builds on HA.
3. Use **version 0.1.4 or newer** for MQTT egg counts.
4. Leave **Protection mode enabled**. Keep `capture_enabled` and `mqtt_enabled`
   off until the steps below are complete.

## 2. Connect the transfer CLI

### Enable the transfer port

On **Settings → Apps/Add-ons → Munai → Configuration**:

1. Enable **HTTPS reference transfers** (`transfer_enabled`).
2. Find the **Network** section below the app options.
3. Beside **`8443/tcp`**, enter **`8443`** in the host-port field.
4. Save and restart Munai.

This is an **HA app setting, not router port forwarding**. Use it only on your
LAN/VPN. Port **8123 is HA's UI**, not Munai's transfer API.

### Pair once

Open Munai's **Logs** and copy the value after
**Transfer certificate SHA256 fingerprint**. This is not the browser certificate.

Replace `HA_HOST` with HA's LAN IP/hostname and `FINGERPRINT` with that value:

```bash
python munai_remote.py remote pair https://HA_HOST:8443 --fingerprint FINGERPRINT
```

The command prints a **token digest**. In Munai's **Configuration**:

1. Enable **Show unused optional configuration options** if the field is hidden.
2. Paste the printed digest into **Transfer token SHA256 (optional)**
   (`transfer_token_sha256`). Do **not** paste the certificate fingerprint here.
3. Set **Transfer client permissions** (`transfer_token_scope`) to **write**.
4. Save and restart Munai.

Check the connection:

```bash
python munai_remote.py remote status
```

Pairing is only needed once; skip it on subsequent uploads. The token is stored
in gitignored `.munai-remote.json` with owner-only permissions. Do not share it
or paste full configuration errors: they can expose passwords.

## 3. Upload references

Include reviewed **empty-nest images** in the active reference set's `empty/`
folder, alongside the named hen folders. Eggs are fine, but no hen may be present.
Use several images covering lighting and straw changes. Background exclusions
are derived automatically from these images; there are no manual mask settings.

Upload the active reviewed reference set:

```bash
python munai_remote.py push references --dry-run
python munai_remote.py push references
python munai_remote.py remote status
```

**Check:** `reference_count` should match the uploaded set and `references_ready`
should be `true`. Reviewed empty images and at least two hen identities are required.

Unchanged images are skipped; nothing is deleted. Captured predictions and renamed
corrections are **not automatically promoted to references**. Review and import
new reference images separately using the [reference workflow](DOCS.md#3-configure-recognition-and-upload-reviewed-references).

## 4. Enable MQTT

In Munai's HA **Configuration**, set:

| Setting | Value |
| --- | --- |
| `mqtt_host` | `core-mosquitto` for HA's Mosquitto app, or your broker's hostname/IP. **No `http://`, port, or trailing `/`.** |
| `mqtt_port` | `1883` for plain MQTT, or your TLS broker's port |
| `mqtt_user` / `mqtt_password` | Your broker credentials |
| `mqtt_tls` | Enable only when the broker uses TLS with a trusted certificate |
| `device_name` | The name you want to see in HA |
| `mqtt_enabled` | On |

Keep `device_id` stable. Save and restart Munai.

**Check:** under **Settings → Devices & services → MQTT → Devices**, find your
Munai device and its **Occupant** and **Egg count** sensors. They are unavailable
until a fresh capture is published. HA's MQTT integration must be configured and
discovery enabled.

## 5. Enable capture

Disable any old cron capture schedule using the same camera before enabling HA
capture. Keep the old setup available for rollback.

In Munai's HA **Configuration**, set:

| Setting | Starting value |
| --- | --- |
| `camera_url` | Your complete RTSP URL, including credentials and stream path |
| `capture_interval` | `60` seconds |
| `capture_timeout` | `20` seconds; allowed range **1–60**, never negative |
| `expire_after` | `180` seconds |
| `store_captures` | On if you want saved images to review; otherwise off |
| `capture_enabled` | On |

Save and restart. Allow one capture interval for the first observation.

**Check:** Occupant shows an identity, `empty`, or `unknown`. Failed/stale
observations become `unavailable`; expiry must exceed interval + timeout + 10.
Logs show occupant changes, failures, and recovery—not a line for every frame.

Before relying on Egg count, calibrate **Minimum egg area** (`egg_min_area`) and
**Maximum egg area** (`egg_max_area`) using reviewed egg-free and egg-containing
images. Areas use the 640×360 processing frame; generic defaults are not a camera
calibration. Use offline `--min-area` / `--max-area` checks to select the HA values.
The count is per frame, not cumulative; zero while occupied does not mean there
are no hidden eggs. Both sensors become unavailable if observations go stale.

Watch HA responsiveness. Once it works reliably, enable **Start on boot** on
Munai's Info page. If it causes trouble, stop Munai before restoring the cron job.

## 6. Review and maintain

Download saved HA images into an ignored local directory:

```bash
python munai_remote.py pull captures local-data/ha-captures
```

Images are grouped as `empty/`, `unknown/`, and `<identity>/unreviewed/`.
Automatic identity predictions remain unreviewed until you check them.

After adding reviewed references locally, repeat **`push references`**; no
re-pairing is needed. Take an **encrypted HA backup including Munai** before
updates or uninstalling. Retention removes old captures/results, never reviewed
references; uninstalling can remove the app's data.

## If something fails

| Problem | Check |
| --- | --- |
| Cannot connect to HTTPS | Munai is running; `transfer_enabled` is on; host port **8443** is mapped under **Network**; use `https://HA_HOST:8443`, not port 8123. Use a LAN IP if `.local` DNS fails inside the container. |
| No Transfer token SHA256 field | Enable **Show unused optional configuration options**. Check **Info** shows **0.1.2+**; reload the app repository and update if older. |
| Token rejected / HTTP 401 | Paste the **token digest**, not the certificate fingerprint; use write permissions, save and restart. |
| Certificate fingerprint changed | Verify the fingerprint in trusted Munai logs. Do not bypass the check. |
| Reference rejected | Check the image, empty references and recognition settings. References need measurable hen/background detail. |
| No MQTT device or fresh state | Check broker host format, credentials, HA's MQTT integration, and Munai's logs. |

[Advanced settings, storage, backups and offline tools](DOCS.md) ·
[Legacy Pi/cron instructions](LEGACY.md)
