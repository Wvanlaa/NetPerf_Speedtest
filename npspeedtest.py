# netperf_speedtest - AutoInstall ! Prioritize existing WAN Profiles (break up duplicate matches for control) by TCP download (netperf)

from csclient import EventingCSClient
import time
import datetime


class AutoInstallException(Exception):
    pass


class Timeout(AutoInstallException):
    pass


class SocketLost(AutoInstallException):
    pass


class OneModem(AutoInstallException):
    pass


class RunBefore(AutoInstallException):
    pass


class AutoInstall(object):
    MIN_DOWNLOAD_SPD = 0.0  # Mbps
    MIN_UPLOAD_SPD = 0.0  # Mbps
    SCHEDULE = 0  # Run AutoInstall every {SCHEDULE} minutes. 0 = Only run on boot.
    NUM_ACTIVE_SIMS = 0  # Number of fastest (download) SIMs to keep active.  0 = all; do not disable SIMs
    ONLY_RUN_ONCE = False  # True means do not run if AutoInstall has been run on this device before.

    STATUS_DEVS_PATH = '/status/wan/devices'
    CFG_RULES2_PATH = '/config/wan/rules2'
    CTRL_WAN_DEVS_PATH = '/control/wan/devices'
    API_URL = 'https://www.cradlepointecm.com/api/v2'
    CONNECTION_STATE_TIMEOUT = 15 * 60  # 7 Min
    NETPERF_TIMEOUT = 5 * 60  # 5 Min
    sims = {}

    def __init__(self):
        self.client = EventingCSClient('AutoInstall')

    def check_if_run_before(self):
        if self.ONLY_RUN_ONCE:
            if self.client.get('/config/wan/rules2/0/_id_') == '00000000-1234-1234-1234-1234567890ab':
                self.client.log(
                    'ERROR - AutoInstall has been run before!')
                raise RunBefore(
                    'ERROR - AutoInstall has been run before!')
        return False

    def wait_for_ncm_sync(self):
        # WAN connection_state
        if self.client.get('status/wan/connection_state') != 'connected':
            self.client.log('Waiting until WAN is connected...')
        timeout_count = 500
        while self.client.get('/status/wan/connection_state') != 'connected':
            timeout_count -= 1
            if not timeout_count:
                raise Timeout('WAN not connecting')
            time.sleep(2)

        # ECM State
        if self.client.get('status/ecm/state') != 'connected':
            self.client.log('Waiting until NCM is connected...')
            self.client.put('/control/ecm', {'start': True})
        timeout_count = 500
        while self.client.get('/status/ecm/state') != 'connected':
            timeout_count -= 1
            if not timeout_count:
                raise Timeout('NCM not connecting')
            time.sleep(2)

        # ECM Sync
        if self.client.get('status/ecm/sync') != 'ready':
            self.client.log('Waiting until NCM is synced...')
            self.client.put('/control/ecm', {'start': True})
        timeout_count = 500
        while self.client.get('/status/ecm/sync') != 'ready':
            self.client.put('/control/ecm', {'start': True})
            timeout_count -= 1
            if not timeout_count:
                raise Timeout('NCM not connecting')
            time.sleep(2)

        return

    def NCM_suspend(self):
        self.client.log('Stopping NCM')
        timeout_count = 500
        while not 'ready' == self.client.get('/status/ecm/sync'):
            timeout_count -= 1
            if not timeout_count:
                raise Timeout('NCM sync not ready')
            time.sleep(2)
        self.client.put('/control/ecm', {'stop': True})
        timeout_count = 500
        while not 'stopped' == self.client.get('/status/ecm/state'):
            timeout_count -= 1
            if not timeout_count:
                raise Timeout('NCM not stopping')
            time.sleep(2)

    def find_sims(self):
        timeout = 0
        while True:
            sims = {}
            wan_devs = self.client.get(self.STATUS_DEVS_PATH) or {}
            for uid, status in wan_devs.items():
                if uid.startswith('mdm-'):
                    error_text = status.get('status', {}).get('error_text', '')
                    if error_text:
                        if 'NOSIM' in error_text:
                            continue
                    sims[uid] = status
            num_sims = len(sims)
            if not num_sims:
                self.client.log('No SIMs found at all yet')
            if num_sims < 2:
                self.client.log('Only 1 SIM found!')
            if timeout >= 10:
                self.client.log('Timeout: Did not find 2 or more SIMs')
                raise Timeout('Did not find 2 or more SIMs')
            if num_sims >= 2:
                break
            time.sleep(10)
            timeout += 1

        self.client.log(f'Found SIMs: {sims.keys()}')
        self.sims = sims
        return True

    def check_rule(self, dev_UID):
        i = 0.1
        found_self = False
        for dev, stat in self.sims.items():
            # self.client.log(f'RULE IDS: {self.sims[dev_UID]["rule_id"]} -- {stat.get("config", {}).get("_id_")}')
            if stat.get('config', {}).get('_id_') == self.sims[dev_UID]["rule_id"]:
                if not found_self:
                    found_self = True
                else:  # Two SIMs using same WAN profile
                    config = self.client.get(f'config/wan/rules2/{self.sims[dev_UID]["rule_id"]}')
                    config.pop('_id_')
                    config['priority'] += i
                    i += 0.1
                    config['trigger_name'] = f'{stat["info"]["port"]} {stat["info"]["sim"]}'
                    config['trigger_string'] = f'type|is|mdm%sim|is|{stat["info"]["sim"]}' \
                        f'%port|is|{stat["info"]["port"]}'
                    self.client.log(f'NEW WAN RULE: {config}')
                    rule_index = self.client.post('config/wan/rules2/', config)["data"]
                    new_id = self.client.get(f'config/wan/rules2/{rule_index}/_id_')
                    self.sims[dev_UID]["config"]["_id_"] = new_id
                    # self.client.log(f'new_id: {new_id}')
                    return True
        return False
                    
    def separate_profiles(self):
        repeat = True
        while repeat:
            self.find_sims()
            for dev_UID, dev_status in self.sims.items():
                try:
                    rule_id = dev_status.get('config', {}).get('_id_')
                    priority = float(dev_status.get('config', {}).get('priority'))
                    port = dev_status.get('info', {}).get('port')
                    sim = dev_status.get('info', {}).get('sim')
                    self.sims[dev_UID]["rule_id"] = rule_id
                    self.sims[dev_UID]["priority"] = priority
                    self.sims[dev_UID]["port"] = port
                    self.sims[dev_UID]["sim"] = sim
                    repeat = self.check_rule(dev_UID)
                except Exception as e:
                    self.client.log(f'Exception: {e}')
                    continue

    def modem_state(self, sim, state):
        # Blocking call that will wait until a given state is shown as the modem's status
        timeout_counter = 0
        sleep_seconds = 0
        conn_path = '%s/%s/status/connection_state' % (self.STATUS_DEVS_PATH, sim)
        self.client.log(f'Connecting {self.port_sim(sim)}')
        while True:
            sleep_seconds += 5
            conn_state = self.client.get(conn_path)
            self.client.log(f'Waiting for {self.port_sim(sim)} to connect.  Current State={conn_state}')
            if conn_state == state:
                break
            if timeout_counter > self.CONNECTION_STATE_TIMEOUT:
                self.client.log(f'Timeout waiting on {self.port_sim(sim)}')
                raise Timeout(conn_path)
            time.sleep(min(sleep_seconds, 45))
            timeout_counter += sleep_seconds
        self.client.log(f'{self.port_sim(sim)} connected.')
        return True

    def iface(self, sim):
        iface = self.client.get('%s/%s/info/iface' % (self.STATUS_DEVS_PATH, sim))
        return iface

    def port_sim(self, sim):
        return f'{self.sims[sim]["info"]["port"]} {self.sims[sim]["info"]["sim"]}'

    def run_speedtest(self, speedtest):
        self.client.put('/state/system/netperf', {"run_count": 0})
        res = self.client.put("/control/netperf", speedtest)
        self.client.log(f'Starting Speedtest... {res}')

        timeout_counter = 0
        # wait for results
        delay = speedtest['input']['options']['limit']['time'] + 8
        status_path = "/control/netperf/output/status"
        while True:
            time.sleep(delay)
            status = self.client.get(status_path)
            if status == 'complete':
                break
            if timeout_counter > self.NETPERF_TIMEOUT:
                self.client.log(f"Timeout waiting on speedtest for {speedtest['input']['options']['ifc_wan']}")
                raise Timeout(status_path)
            timeout_counter += delay

        if status != 'complete':
            self.client.log(f"ERROR: status=%s expected 'complete' {status}")
            return None

        # now get the result
        results_path = self.client.get("/control/netperf/output/results_path")

        results = None
        while not results:
            results = self.client.get(results_path)
            time.sleep(2)
        self.client.log('Speedtest Complete.')
        return results

    def do_speedtest(self, sim):
        try:
            iface = cp.get(f'status/wan/{sim}/info/iface')
            speedtest_settings = {
                "input": {
                    "options": {
                        "limit": {
                            "size": 0,
                            "time": 10
                        },
                        "port": None,
                        "fwport": None,
                        "host": "",
                        "ifc_wan": iface,
                        "tcp": True,
                        "udp": False,
                        "send": False,
                        "recv": True,
                        "rr": False
                    },
                    "tests": None
                },
                "run": 1
            }
            tcp_down = self.run_speedtest(speedtest_settings).get('tcp_down')
            speedtest_settings['input']['options']['send'] = True
            speedtest_settings['input']['options']['recv'] = False
            tcp_up = self.run_speedtest(speedtest_settings).get('tcp_up')

            # Single retry if needed:
            if not tcp_up:
                cp.log('tcp_up results missing!')
                speedtest_settings['input']['options']['send'] = True
                speedtest_settings['input']['options']['recv'] = False
                results = self.run_speedtest(speedtest_settings)
                tcp_up = results.get('tcp_up') or None

            if not tcp_down:
                cp.log('tcp_down results missing!')
                speedtest_settings['input']['options']['send'] = False
                speedtest_settings['input']['options']['recv'] = True
                results = self.run_speedtest(speedtest_settings)
                tcp_down = results.get('tcp_down') or None

            down = float(tcp_down.get('THROUGHPUT', 0.0)) if tcp_down else 0.0
            up = float(tcp_up.get('THROUGHPUT', 0.0)) if tcp_up else 0.0
        except Exception as e:
            cp.log(e)
            down, up = 0, 0
        return down, up

    def test_sim(self, device):
        try:
            if self.modem_state(device, 'connected'):

                # Get diagnostics and log it
                diagnostics = self.client.get(f'{self.STATUS_DEVS_PATH}/{device}/diagnostics')
                self.sims[device]['diagnostics'] = diagnostics
                self.client.log(
                    f'Modem Diagnostics: {self.port_sim(device)} RSRP:{diagnostics.get("RSRP")}')

                # Do speedtest and log results
                self.sims[device]['download'], self.sims[device]['upload'] = self.do_speedtest(
                    self.sims[device])
                self.client.log(
                    f'Speedtest Results: {self.port_sim(device)} TCP Download: '
                    f'{self.sims[device]["download"]}Mbps TCP Upload: {self.sims[device]["upload"]}Mbps')

                # Verify minimum speeds
                if self.sims[device].get('download', 0.0) > self.MIN_DOWNLOAD_SPD and self.sims[device].get('upload',
                                                                                                            0.0) > self.MIN_UPLOAD_SPD:
                    return True
                else:  # Did not meet minimums
                    self.client.log(f'{self.port_sim(device)} Failed to meet minimums! MIN_DOWNLOAD_SPD: {self.MIN_DOWNLOAD_SPD} MIN_UPLOAD_SPD: {self.MIN_UPLOAD_SPD}')
                    return False
        except Timeout:
            message = f'Timed out running speedtest on {self.port_sim(device)}'
            self.client.log(message)
            self.client.alert(message)
            self.sims[device]['download'] = self.sims[device]['upload'] = 0.0
            return False

    def create_message(self, uid, *args):
        message = ''
        try:
            for arg in args:
                if arg == 'download':
                    message = "DL:{:.2f}Mbps".format(self.sims[uid]['download']) if not message else ' '.join(
                        [message, "DL:{:.2f}Mbps".format(self.sims[uid]['download'])])
                elif arg == 'upload':
                    message = "UL:{:.2f}Mbps".format(self.sims[uid]['upload']) if not message else ' '.join(
                        [message, "UL:{:.2f}Mbps".format(self.sims[uid]['upload'])])
                elif arg in ['PRD', 'HOMECARRID', 'RFBAND']:  # Do not include labels for these fields
                    message = "{}".format(self.sims[uid]['diagnostics'].get(arg)) if not message else ' '.join(
                        [message, "{}".format(self.sims[uid]['diagnostics'].get(arg))])
                else:  # Include field labels (e.g. "RSRP:-82")
                    message = "{}:{}".format(arg, self.sims[uid]['diagnostics'].get(arg)) if not message else ' '.join(
                        [message, "{}:{}".format(arg, self.sims[uid]['diagnostics'].get(arg))])
        except Exception as e:
            self.client.log(e)
        return message

    def prioritize_rules(self, sim_list):
        lowest_priority = 100
        for uid in sim_list:
            priority = self.client.get(f'status/wan/devices/{uid}/config/priority')
            if priority < lowest_priority:
                lowest_priority = priority
        for i, uid in enumerate(sim_list):
            rule_id = self.client.get(f'status/wan/devices/{uid}/config/_id_')
            new_priority = lowest_priority + i * .1
            self.client.log(f'New priority for {uid} = {new_priority}')
            self.client.put(f'config/wan/rules2/{rule_id}/priority', new_priority)
        return

    def run(self):  # *** Main Application Starts Here ***
        self.client.log(
            f'AutoInstall Starting... MIN_DOWNLOAD_SPD:{self.MIN_DOWNLOAD_SPD} MIN_UPLOAD_SPD:{self.MIN_UPLOAD_SPD} '
            f'SCHEDULE:{self.SCHEDULE} NUM_ACTIVE_SIMS:{self.NUM_ACTIVE_SIMS} ONLY_RUN_ONCE:{self.ONLY_RUN_ONCE}')

        self.check_if_run_before()

        self.wait_for_ncm_sync()

        # Get info from router
        product_name = self.client.get("/status/product_info/product_name")
        system_id = self.client.get("/config/system/system_id")
        router_id = self.client.get('status/ecm/client_id')

        # Send startup alert
        message = f'AutoInstall Starting! {system_id} - {product_name} - Router ID: {router_id}'
        self.client.log(f'Sending alert to NCM: {message}')
        self.client.alert(message)

        self.separate_profiles()

        # Pause for 3 seconds to allow NCM Alert to be sent before suspending NCM
        time.sleep(3)
        self.NCM_suspend()

        success = False  # AutoInstall Success Status - Becomes True when a SIM meets minimum speeds

        # Test the connected SIM first
        primary_device = self.client.get('status/wan/primary_device')
        if 'mdm-' in primary_device:  # make sure its a modem
            if self.test_sim(primary_device):
                success = True

        # Disable all wan rules
        wan_rules = self.client.get('config/wan/rules2')
        for i, uid in enumerate(wan_rules):
            self.client.put(f'config/wan/rules2/{i}/disabled', True)
        time.sleep(5)

        # test remaining SIMs
        for sim in self.sims:
            if not self.sims[sim].get('download'):
                rule_id = self.client.get(f'status/wan/devices/{sim}/config/_id_')
                self.client.put(f'config/wan/rules2/{rule_id}/disabled', False)
                if self.test_sim(sim):
                    success = True
                self.client.put(f'config/wan/rules2/{rule_id}/disabled', True)

        # Prioritizes SIMs based on download speed
        sorted_results = sorted(self.sims, key=lambda x: self.sims[x]['download'], reverse=True)

        # Configure WAN Profiles
        self.client.log(f'Prioritizing SIMs: {sorted_results}')
        self.prioritize_rules(sorted_results)

        # Enable all wan rules
        wan_rules = self.client.get('config/wan/rules2')
        for i, uid in enumerate(wan_rules):
            self.client.put(f'config/wan/rules2/{i}/disabled', False)
        time.sleep(3)

        # Build results text
        results_text = datetime.datetime.now().strftime('%m/%d/%y %H:%M:%S')  # Start with a timestamp
        if not success:
            results_text += f' FAILED TO MEET MINIMUMS! MIN_DOWNLOAD_SPD:{self.MIN_DOWNLOAD_SPD} MIN_UPLOAD_SPD:{self.MIN_UPLOAD_SPD}'
        for uid in sorted_results:  # Add the results of each SIM with the fields specified:
            results_text = ' | '.join(
                [results_text, self.create_message(uid, 'PRD', 'HOMECARRID', 'RFBAND', 'RSRP', 'download', 'upload')])

        # put results to description field
        self.client.put('config/system/asset_id', results_text[:255])

        # Complete!  Send results.
        message = f"AutoInstall Complete! {system_id} Results: {results_text}"
        self.wait_for_ncm_sync()
        self.client.log(message)
        self.client.alert(message)


def manual_test(path, asset_id, *args):
    if asset_id.lower() in ['go', 'start', 'run']:
        try:
            autoinstall.run()
        except Exception as err:
            autoinstall.client.log(f"Failed with exception={type(err)} err={str(err)}")
        finally:
            autoinstall.client.put('/control/ecm', {'start': 'true'})


if __name__ == '__main__':
    cp = EventingCSClient('AutoInstall')
    while not cp.get('status/wan/connection_state') == 'connected':
        time.sleep(1)
    autoinstall = AutoInstall()
    try:
        autoinstall.client.on('put', 'config/system/asset_id', manual_test)
        autoinstall.client.log('Waiting... enter "start" into asset_id to start AutoInstall.')
        while True:
            time.sleep(123)
    except Exception as err:
        autoinstall.client.log(f"Failed with exception={type(err)} err={str(err)}")
