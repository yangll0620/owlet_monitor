#!/Users/tongjin/miniconda3/bin/python
#
# Dependencies (Linux):
# $ pip3 install python-jwt gcloud sseclient requests_toolbelt
#
# Extra dependencies (Windows 10):
# $ pip3 install pycryptodome

import sys, os, time, requests, json
import getpass
import logging

sess = None
url_props = None
url_activate = None
#headers = {'Content-Type' : 'application/json', 'Accept' : 'application/json'}
headers = {}
auth_token = None
expire_time = 0
dsn = None
owlet_region = 'world'
region_config = {
    'world': {
        'url_mini': 'https://ayla-sso.owletdata.com/mini/',
        'url_signin': 'https://user-field-1a2039d9.aylanetworks.com/api/v1/token_sign_in',
        'url_base': 'https://ads-field-1a2039d9.aylanetworks.com/apiv1',
        'apiKey': 'AIzaSyCsDZ8kWxQuLJAMVnmEhEkayH1TSxKXfGA',
        'app_id': 'sso-prod-3g-id',
        'app_secret': 'sso-prod-UEjtnPCtFfjdwIwxqnC0OipxRFU',
    },
    'europe': {
        'url_mini': 'https://ayla-sso.eu.owletdata.com/mini/',
        'url_signin': 'https://user-field-eu-1a2039d9.aylanetworks.com/api/v1/token_sign_in',
        'url_base': 'https://ads-field-eu-1a2039d9.aylanetworks.com/apiv1',
        'apiKey': 'AIzaSyDm6EhV70wudwN3iOSq3vTjtsdGjdFLuuM',
        'app_id': 'OwletCare-Android-EU-fw-id',
        'app_secret': 'OwletCare-Android-EU-JKupMPBoj_Npce_9a95Pc8Qo0Mw',
    }
}

class FatalError(Exception):
    pass

def log(s):
    sys.stderr.write(s + '\n')
    sys.stderr.flush()

def record(s):
    sys.stdout.write(s + '\n')
    sys.stdout.flush()

def login():
    global auth_token, expire_time, owlet_region
    try:
        sys.stderr.write("Enter your user account \n")
        owlet_user = input()
        owlet_pass = getpass.getpass()
        if not len(owlet_user):
            raise FatalError("OWLET_USER is empty")
        if not len(owlet_pass):
            raise FatalError("OWLET_PASS is empty")
    except KeyError as e:
        raise FatalError("OWLET_USER or OWLET_PASS env var is not defined")
    
    owlet_region = "world"
    if auth_token is not None and (expire_time > time.time()):
        return
    log('Logging in')
    # authenticate against Firebase, get the JWT.
    # need to pass the X-Android-Package and X-Android-Cert headers because
    # the API key is restricted to the Owlet Android app
    # https://cloud.google.com/docs/authentication/api-keys#api_key_restrictions
    api_key = region_config[owlet_region]['apiKey']
    r = requests.post(f'https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword?key={api_key}',
            data=json.dumps({'email': owlet_user, 'password': owlet_pass, 'returnSecureToken': True}),
            headers={
                'X-Android-Package': 'com.owletcare.owletcare',
                'X-Android-Cert': '2A3BC26DB0B8B0792DBE28E6FFDC2598F9B12B74'
        })
    r.raise_for_status()
    jwt = r.json()['idToken']
    # authenticate against owletdata.com, get the mini_token
    r = requests.get(region_config[owlet_region]
                     ['url_mini'], headers={'Authorization': jwt})
    r.raise_for_status()
    mini_token = r.json()['mini_token']
    # authenticate against Ayla, get the access_token
    r = requests.post(region_config[owlet_region]['url_signin'], json={
                "app_id": region_config[owlet_region]['app_id'],
                "app_secret": region_config[owlet_region]['app_secret'],
                "provider": "owl_id",
                "token": mini_token,
                })
    r.raise_for_status()
    auth_token = r.json()['access_token']
    # we will re-auth 60 seconds before the token expires
    expire_time = time.time() + r.json()['expires_in'] - 60
    headers['Authorization'] = 'auth_token ' + auth_token
    log('Auth token %s' % auth_token)

def fetch_dsn():
    global dsn, url_props, url_activate
    if dsn is None:
        log('Getting DSN')
        r = sess.get(region_config[owlet_region]
                     ['url_base'] + '/devices.json', headers=headers)
        r.raise_for_status()
        devs = r.json()
        if len(devs) < 1:
            raise FatalError('Found zero Owlet monitors')
        # Allow for multiple devices
        dsn = []
        url_props = []
        url_activate = []
        for device in devs:
            device_sn = device['device']['dsn']
            dsn.append(device_sn)
            log('Found Owlet monitor device serial number %s' % device_sn)
            url_props.append(
                region_config[owlet_region]['url_base'] + '/dsns/' + device_sn
                + '/properties.json'
            )
            url_activate.append(
                region_config[owlet_region]['url_base'] + '/dsns/' + device_sn
                + '/properties/APP_ACTIVE/datapoints.json'
            )

def reactivate(url_activate):
    payload = { "datapoint": { "metadata": {}, "value": 1 } }
    r = sess.post(url_activate, json=payload, headers=headers)
    r.raise_for_status()

def fetch_props():
    # Ayla cloud API data is updated only when APP_ACTIVE periodically reset to 1.
    my_props = []
    # Get properties for each device; note no pause between requests for each device
    for device_sn,next_url_activate,next_url_props in zip(dsn,url_activate,url_props):
        reactivate(next_url_activate)
        device_props = {'DSN':device_sn}
        r = sess.get(next_url_props, headers=headers)
        r.raise_for_status()
        props = r.json()
        for prop in props:
            n = prop['property']['name']
            del(prop['property']['name'])
            device_props[n] = prop['property']
        my_props.append(device_props)
    return my_props

def record_vitals(p):
    #import pdb;pdb.set_trace()
    device_sn = p['DSN']
    #charge_status = p['CHARGE_STATUS']['value']
    charge_status = 0
    #base_station_on = p['BASE_STATION_ON']['value']
    base_station_on = 1
    data = p['REAL_TIME_VITALS']['value']
    data = json.loads(data)
    heart = data["hr"]
    oxy = data["ox"]
    mov = data["mv"]
    disp = "%d, " % time.time()

    from datetime import datetime
    disp += datetime.now().strftime("%m/%d/%Y, %H:%M:%S")

    if charge_status >= 1:
        disp += "sock charging (%d)" % charge_status
        # base_station_on is (always?) 1 in this case
    elif charge_status == 0:
        if base_station_on == 0:
            # sock was unplugged, but user did not turn on the base station.
            # heart and oxygen levels appear to be reported, but we can't
            # yet assume the sock was placed on the baby's foot.
            disp += "sock not charging, base station off"
        elif base_station_on == 1:
            # base station was intentionally turned on, the sock is presumably
            # on the baby's foot, so we can trust heart and oxygen levels
            disp += ", " + str(heart) + ", " + str(oxy) + ", " + str(mov) + ", " + device_sn
            record(disp)
        else:
            raise FatalError("Unexpected base_station_on=%d" % base_station_on)
    log("%s Status: " % device_sn + disp)


def loop():
    global sess
    sess = requests.session()
    login()
    while True:
        try:   
            fetch_dsn()
            for prop in fetch_props():
                record_vitals(prop)
            time.sleep(2)
        except requests.exceptions.RequestException as e:
            log('Network error: %s' % e)
            time.sleep(1)
            sess = requests.session()

def main():
    try:  
        loop()
    except FatalError as e:
        sys.stderr.write('%s\n' % e)
        sys.exit(1)

if __name__ == "__main__":
    main()
