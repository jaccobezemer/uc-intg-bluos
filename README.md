# BluOS Integration for Unfolded Circle Remote 3

This integration allows you to control BluOS-enabled devices (like NAD M33, Bluesound players, etc.) over the network with the Unfolded Circle Remote 3.

## Features

- Play/Pause/Stop control
- Volume control (0-100%)
- Mute function
- Input/preset selection
- Status monitoring

## Installation on UC Remote 3

1. **Download** the latest release: [Releases](https://github.com/jaccobezemer/uc-intg-bluos/releases)
2. **Open** Remote 3 web configurator: `http://[remote-ip]/configurator`
3. **Go to** Integrations → Add Integration → Install Custom
4. **Upload** the `.tar.gz` file

## Setup Process

1. After uploading the integration file, click **"Setup"** on the integration's card.
2. The integration scans your network for BluOS devices (this may take a few seconds).
3. Select your device from the list, or choose **"Setup Manually"** to enter its IP address directly.
4. Click **"Done"** to finish. Repeat the process to add additional devices.

## BluOS API Protocol

BluOS devices use a REST API via HTTP on port 11000.

Base URL format: `http://{ip}:11000/{endpoint}`

Examples:
- Get status: `GET http://192.168.2.238:11000/Status`
- Set volume: `GET http://192.168.2.238:11000/Volume?level=50`
- Play: `GET http://192.168.2.238:11000/Play`
- Pause: `GET http://192.168.2.238:11000/Pause`
