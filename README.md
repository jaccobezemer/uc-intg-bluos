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

The setup is a two-step process:

### Step 1: Initial Setup (during installation)

1. After uploading the integration file, the setup process will start automatically.
2. You don't need to enter any information here. Just click **"Done"** to complete the installation.

### Step 2: Discover and Add Devices

1. Go to the integration's card in the configurator and click **"Setup"** again.
2. The integration will now scan your network for available BluOS devices. This may take a few seconds.
3. Once discovery is complete, the integration will automatically add all found devices as entities.

## BluOS API Protocol

BluOS devices use a REST API via HTTP on port 11000.

Base URL format: `http://{ip}:11000/{endpoint}`

Examples:
- Get status: `GET http://192.168.2.238:11000/Status`
- Set volume: `GET http://192.168.2.238:11000/Volume?level=50`
- Play: `GET http://192.168.2.238:11000/Play`
- Pause: `GET http://192.168.2.238:11000/Pause`
