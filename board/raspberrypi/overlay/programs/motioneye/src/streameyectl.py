
# Copyright (c) 2015 Calin Crisan
# This file is part of motionPie.
#
# motionEye is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>. 

import config
import hashlib
import logging
import os.path
import re

from tornado.ioloop import IOLoop

import settings

from config import additional_config


MOTIONEYE_CONF = '/data/etc/motioneye.conf'
RASPIMJPEG_CONF = '/data/etc/raspimjpeg.conf'
NGINX_CONF = '/data/etc/nginx.conf'
NGINX_AUTH = '/data/etc/nginx.auth'

EXPOSURE_CHOICES = [
    ('off', 'Off'),
    ('auto', 'Auto'),
    ('night', 'Night'),
    ('nightpreview', 'Night Preview'),
    ('backlight', 'Backlight'),
    ('spotlight', 'Spotlight'),
    ('sports', 'Sports'),
    ('snow', 'Snow'),
    ('beach', 'Beach'),
    ('verylong', 'Very Long'),
    ('fixedfps', 'Fixed FPS'),
    ('antishake', 'Antishake'),
    ('fireworks', 'Fireworks')
]

AWB_CHOICES = [
    ('off', 'Off'),
    ('auto', 'Auto'),
    ('sunlight', 'Sunlight'),
    ('cloudy', 'Cloudy'),
    ('shade', 'Shade'),
    ('tungsten', 'Tungsten'),
    ('fluorescent', 'Fluorescent'),
    ('incandescent', 'Incandescent'),
    ('flash', 'Flash'),
    ('horizon', 'Horizon')
]

METERING_CHOICES = [
    ('average', 'Average'),
    ('spot', 'Spot'),
    ('backlit', 'Backlit'),
    ('matrix', 'Matrix')
]

DRC_CHOICES = [
    ('off', 'Off'),
    ('low', 'Low'),
    ('medium', 'Medium'),
    ('hight', 'High')
]

IMXFX_CHOICES = [
    ('none', 'None'),
    ('negative', 'Negative'),
    ('solarize', 'Solarize'),
    ('sketch', 'Sketch'),
    ('denoise', 'Denoise'),
    ('emboss', 'Emboss'),
    ('oilpaint', 'Oilpaint'),
    ('hatch', 'Hatch'),
    ('gpen', 'G Pen'),
    ('pastel', 'Pastel'),
    ('watercolor', 'Water Color'),
    ('film', 'Film'),
    ('blur', 'Blur'),
    ('saturation', 'Saturation'),
    ('colorswap', 'Color Swap'),
    ('washedout', 'Washed Out'),
    ('posterise', 'Posterize'),
    ('colorpoint', 'Color Point'),
    ('colorbalance', 'Color Balance'),
    ('cartoon', 'Cartoon'),
    ('deinterlace1', 'Deinterlace 1'),
    ('deinterlace2', 'Deinterlace 2')
]

RESOLUTION_CHOICES = [
    ('320x200', '320x200'),
    ('320x240', '320x240'),
    ('640x480', '640x480'),
    ('800x480', '800x480'),
    ('800x600', '800x600'),
    ('1024x576', '1024x576'),
    ('1024x768', '1024x768'),
    ('1280x720', '1280x720'),
    ('1280x800', '1280x800'),
    ('1280x960', '1280x960'),
    ('1280x1024', '1280x1024'),
    ('1440x960', '1440x960'),
    ('1440x1024', '1440x1024'),
    ('1600x1200', '1600x1200'),
    ('1920x1080', '1920x1080')
]

ROTATION_CHOICES = [
    ('0', '0&deg;'),
    ('90', '90&deg;'),
    ('180', '180&deg;'),
    ('270', '270&deg;')
]

AUTH_CHOICES = [
    ('disabled', 'Disabled'),
    ('basic', 'Basic'),
    ('digest', 'Digest')
]

_stream_eye_enabled = None


def _get_stream_eye_enabled():
    global _stream_eye_enabled
    
    if _stream_eye_enabled is not None:
        return _stream_eye_enabled

    camera_ids = config.get_camera_ids(filter_valid=False) # filter_valid prevents infinte recursion
    if len(camera_ids) != 1:
        _stream_eye_enabled = False
        return False
    
    camera_config = config.get_camera(camera_ids[0], as_lines=True) # as_lines prevents infinte recursion
    camera_config = config._conf_to_dict(camera_config)
    if camera_config.get('@proto') != 'mjpeg':
        _stream_eye_enabled = False
        return False
    if '127.0.0.1:' not in camera_config.get('@url', ''):
        _stream_eye_enabled = False
        return False

    _stream_eye_enabled = True
    return True


def _set_stream_eye_enabled(enabled):
    was_enabled = _get_stream_eye_enabled()
    if enabled and not was_enabled:
        io_loop = IOLoop.instance()
        io_loop.add_callback(_set_stream_eye_enabled_deferred, True)
        
    elif not enabled and was_enabled:
        io_loop = IOLoop.instance()
        io_loop.add_callback(_set_stream_eye_enabled_deferred, False)
        
    # this will force updating nginx settings whenever the surveillance credentials are changed
    nginx_settings = _get_nginx_settings(1)
    _set_nginx_settings(1, nginx_settings)


def _set_stream_eye_enabled_deferred(enabled):
    if enabled:
        logging.debug('disabling all cameras')
        for camera_id in config.get_camera_ids():
            camera_config = config.get_camera(camera_id)
            camera_config['@enabled'] = False
            config.set_camera(camera_id, camera_config)
        
        logging.debug('renaming thread files')
        for name in os.listdir(settings.CONF_PATH):
            if re.match('^thread-\d+.conf$', name):
                os.rename(os.path.join(settings.CONF_PATH, name), os.path.join(settings.CONF_PATH, name + '.bak'))

        logging.debug('adding simple mjpeg camera')
        device_details = {
            'proto': 'mjpeg',
            'host': '127.0.0.1',
            'port': '8081',
            'username': 'username', # will be replaced by nginx settings
            'password': 'password',
            'scheme': 'http',
            'uri': '/'
        }
        camera_config = config.add_camera(device_details)
        
        _set_motioneye_add_remove_cameras(False)


    else: # disabled
        logging.debug('removing simple mjpeg camera')
        for camera_id in config.get_camera_ids():
            camera_config = config.get_camera(camera_id)
            if camera_config.get('@proto') == 'mjpeg':
                config.rem_camera(camera_id)

        logging.debug('renaming thread files')
        for name in os.listdir(settings.CONF_PATH):
            if re.match('^thread-\d+.conf.bak$', name):
                os.rename(os.path.join(settings.CONF_PATH, name), os.path.join(settings.CONF_PATH, name[:-4]))
        
        config.invalidate()

        logging.debug('enabling all cameras')
        for camera_id in config.get_camera_ids():
            camera_config = config.get_camera(camera_id)
            camera_config['@enabled'] = True
            config.set_camera(camera_id, camera_config)
            
        _set_motioneye_add_remove_cameras(True)


def _set_motioneye_add_remove_cameras(enabled):
    logging.debug('%s motionEye add/remove cameras' % ['disabling', 'enabling'][enabled])

    lines = []
    found = False
    if os.path.exists(MOTIONEYE_CONF):
        with open(MOTIONEYE_CONF) as f:
            lines = f.readlines()

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
    
            try:
                name, _ = line.split(' ', 2)
    
            except:
                continue
            
            name = name.replace('_', '-')
    
            if name == 'add-remove-cameras':
                lines[i] = 'add-remove-cameras %s' % str(enabled).lower()
                found = True

    if not found:
        lines.append('add-remove-cameras %s' % str(enabled).lower())

    with open(MOTIONEYE_CONF, 'w') as f:
        for line in lines:
            if not line.strip():
                continue
            if not line.endswith('\n'):
                line += '\n'
            f.write(line)


def _get_raspimjpeg_settings(camera_id):
    s = {
        'brightness': 50,
        'contrast': 0,
        'saturation': 0,
        'sharpness': 0,
        'iso': 400,
        'ev': 0,
        'shutter': 0,
        'exposure': 'auto',
        'awb': 'auto',
        'metering': 'average',
        'drc': 'off',
        'vstab': False,
        'imxfx': 'none',
        'width': 640,
        'height': 480,
        'rotation': 0,
        'vflip': False,
        'hflip': False,
        'framerate': 15,
        'quality': 50
    }
    
    if os.path.exists(RASPIMJPEG_CONF):
        logging.debug('reading raspimjpeg settings from %s' % RASPIMJPEG_CONF)

        with open(RASPIMJPEG_CONF) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    name, value = line.split(' ', 1)

                except:
                    continue

                name = name.replace('_', '-')

                try:
                    value = int(value)
                
                except:
                    pass
                    
                if value == 'false':
                    value = False
                    
                elif value == 'true':
                    value = True

                s[name] = value
                    
    s['contrast'] = (s['contrast'] + 100) / 2
    s['saturation'] = (s['saturation'] + 100) / 2
    s['sharpness'] = (s['sharpness'] + 100) / 2
    
    s['resolution'] = '%sx%s' % (s.pop('width'), s.pop('height'))
    
    s = dict(('se' + n[0].upper() + n[1:], v) for (n, v) in s.items())

    return s


def _set_raspimjpeg_settings(camera_id, s):
    s = dict((n[2].lower() + n[3:], v) for (n, v) in s.items())
    
    s['width'] = int(s['resolution'].split('x')[0])
    s['height'] = int(s.pop('resolution').split('x')[1])

    s['contrast'] = s['contrast'] * 2 - 100
    s['saturation'] = s['saturation'] * 2 - 100
    s['sharpness'] = s['sharpness'] * 2 - 100

    logging.debug('writing raspimjpeg settings to %s' % RASPIMJPEG_CONF)

    lines = []
    for name, value in sorted(s.items(), key=lambda i: i[0]):
        if isinstance(value, bool):
            value = str(value).lower()

        line = '%s %s\n' % (name, value)
        lines.append(line)

    with open(RASPIMJPEG_CONF, 'w') as f:
        for line in lines:
            f.write(line)


def _get_nginx_settings(camera_id):
    s = {
        'seAuthMode': 'disabled',
        'sePort': 8081,
    }
    
    if os.path.exists(NGINX_CONF):
        logging.debug('reading nginx settings from %s' % NGINX_CONF)

        with open(NGINX_CONF) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                m = re.findall('listen (\d+)', line)
                if m:
                    s['sePort'] = int(m[0])
                    continue
                    
                if line.count('auth_basic'):
                    s['seAuthMode'] = 'basic'
                    
                elif line.count('auth_digest'):
                    s['seAuthMode'] = 'digest'

    return s


def _set_nginx_settings(camera_id, s):
    s = dict(s)
    s.setdefault('sePort', 8081)
    s.setdefault('seAuthMode', 'disabled')
    
    main_config = config.get_main()
    username = main_config['@normal_username']
    password = main_config['@normal_password']
    realm = 'motionPie'

    logging.debug('writing nginx settings to %s' % NGINX_CONF)
    
    lines = [];
    lines.append('user root root;')
    lines.append('worker_processes 1;')
    lines.append('pid /var/run/nginx.pid;')

    lines.append('events {')
    lines.append('    worker_connections 128;')
    lines.append('}')

    lines.append('http {')
    lines.append('    default_type application/octet-stream;')

    lines.append('    server {')
    lines.append('        listen %s;' % s['sePort'])
    
    if s['seAuthMode'] == 'basic':
        lines.append('        auth_basic "%s";' % realm)
        lines.append('        auth_basic_user_file %s;' % NGINX_AUTH)
        with open(NGINX_AUTH, 'w') as f:
            f.write('%s:{PLAIN}%s' % (username, password))

    elif s['seAuthMode'] == 'digest':
        lines.append('        auth_digest "%s";' % realm)
        lines.append('        auth_digest_user_file %s;' % NGINX_AUTH)
        with open(NGINX_AUTH, 'w') as f:
            pwd_hash = hashlib.md5(':'.join([username, realm, password])).hexdigest()
            f.write('%s:%s:%s' % (username, realm, pwd_hash))

    else: # disabled
        try:
            os.remove(NGINX_AUTH)
        
        except:
            pass

    lines.append('        location / {')
    lines.append('            proxy_pass http://127.0.0.1:8080;')
    lines.append('        }')
    lines.append('    }')
    lines.append('}')

    with open(NGINX_CONF, 'w') as f:
        for line in lines:
            f.write(line + '\n')
    
    # a workaround to update the camera username and password
    # since we cannot call set_camera() from here
    url = 'http://%s:%s@127.0.0.1:%s/' % (username, password, s['sePort'])
    if 1 in config._camera_config_cache:
        logging.debug('updating streaming authentication in config cache')
        config._camera_config_cache[1]['@url'] = url

    lines = config.get_camera(1, as_lines=True)
    for line in lines:
        if line.startswith('# @url'):
            line = '# @url %s' % url

    config_file = os.path.join(settings.CONF_PATH, config._CAMERA_CONFIG_FILE_NAME % {'id': 1})
    logging.debug('updating streaming authentication in camera config file %s' % config_file)
    with open(config_file, 'w') as f:
        for line in lines:
            f.write(line + '\n')
    
    if os.system('streameye.sh restart'):
        logging.error('streameye restart failed')


@additional_config
def streamEyeMainSeparator():
    return {
        'type': 'separator',
        'section': 'expertSettings',
        'advanced': True
    }


@additional_config
def streamEye():
    return {
        'label': 'Fast Network Camera',
        'description': 'Enabling this option will turn your Raspberry PI into a simple and fast MJPEG network camera, ' +
                'disabling motion detection, media files and all other advanced features (works only with the CSI camera)',
        'type': 'bool',
        'section': 'expertSettings',
        'advanced': True,
        'reboot': True,
        'get': _get_stream_eye_enabled,
        'set': _set_stream_eye_enabled,
    }


@additional_config
def streamEyeCameraSeparator1():
    return {
        'type': 'separator',
        'section': 'device',
        'camera': True,
        'advanced': True
    }

 
@additional_config
def seBrightness():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Brightness',
        'description': 'sets a desired brightness level for this camera',
        'type': 'range',
        'min': 0,
        'max': 100,
        'snap': 2,
        'ticksnum': 5,
        'decimals': 0,
        'unit': '%',
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seContrast():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Contrast',
        'description': 'sets a desired contrast level for this camera',
        'type': 'range',
        'min': 0,
        'max': 100,
        'snap': 2,
        'ticksnum': 5,
        'decimals': 0,
        'unit': '%',
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seSaturation():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Saturation',
        'description': 'sets a desired saturation level for this camera',
        'type': 'range',
        'min': 0,
        'max': 100,
        'snap': 2,
        'ticksnum': 5,
        'decimals': 0,
        'unit': '%',
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seSharpness():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Sharpness',
        'description': 'sets a desired sharpness level for this camera',
        'type': 'range',
        'min': 0,
        'max': 100,
        'snap': 2,
        'ticksnum': 5,
        'decimals': 0,
        'unit': '%',
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }



@additional_config
def streamEyeCameraSeparator2():
    return {
        'type': 'separator',
        'section': 'device',
        'camera': True,
        'advanced': True
    }

 
@additional_config
def seResolution():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Video Resolution',
        'description': 'the video resolution (larger values produce better quality but require more CPU power, larger storage space and bandwidth)',
        'type': 'choices',
        'choices': RESOLUTION_CHOICES,
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seRotation():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Video Rotation',
        'description': 'use this to rotate the captured image, if your camera is not positioned correctly',
        'type': 'choices',
        'choices': ROTATION_CHOICES,
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seVflip():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Flip Vertically',
        'description': 'enable this to flip the captured image vertically',
        'type': 'bool',
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seHflip():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Flip Horizontally',
        'description': 'enable this to flip the captured image horizontally',
        'type': 'bool',
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seFramerate():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Frame Rate',
        'description': 'sets the number of frames captured by the camera every second (higher values produce smoother videos but require more CPU power, larger storage space and bandwidth)',
        'type': 'range',
        'min': 1,
        'max': 30,
        'snap': 0,
        'ticks': "1|5|10|15|20|25|30",
        'decimals': 0,
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seQuality():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Image Quality',
        'description': 'sets the JPEG image quality (higher values produce a better image quality but require more storage space and bandwidth)',
        'type': 'range',
        'min': 0,
        'max': 100,
        'snap': 2,
        'ticksnum': 5,
        'decimals': 0,
        'unit': '%',
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def streamEyeCameraSeparator3():
    return {
        'type': 'separator',
        'section': 'device',
        'camera': True,
        'advanced': True
    }

 
@additional_config
def seIso():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'ISO',
        'description': 'sets a desired ISO level for this camera',
        'type': 'range',
        'min': 100,
        'max': 800,
        'snap': 1,
        'ticksnum': 8,
        'decimals': 0,
        'unit': '',
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seEv():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'EV Compensation',
        'description': 'sets a desired EV compensation level for this camera',
        'type': 'range',
        'min': -25,
        'max': 25,
        'snap': 1,
        'ticksnum': 11,
        'decimals': 0,
        'unit': '',
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seShutter():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Shutter Speed',
        'description': 'sets a desired shutter speed for this camera',
        'type': 'number',
        'min': 0,
        'max': 6000000,
        'unit': 'microseconds',
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def streamEyeCameraSeparator4():
    return {
        'type': 'separator',
        'section': 'device',
        'camera': True,
        'advanced': True
    }

 
@additional_config
def seExposure():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Exposure Mode',
        'description': 'sets a desired exposure mode for this camera',
        'type': 'choices',
        'choices': EXPOSURE_CHOICES,
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seAwb():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Automatic White Balance',
        'description': 'sets a desired automatic white balance mode for this camera',
        'type': 'choices',
        'choices': AWB_CHOICES,
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seMetering():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Metering Mode',
        'description': 'sets a desired metering mode for this camera',
        'type': 'choices',
        'choices': METERING_CHOICES,
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seDrc():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Dynamic Range Compensation',
        'description': 'sets a desired dynamic range compensation level for this camera',
        'type': 'choices',
        'choices': DRC_CHOICES,
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seVstab():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Video Stabilization',
        'description': 'enables or disables video stabilization for this camera',
        'type': 'bool',
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def seImxfx():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Image Effect',
        'description': 'sets a desired image effect for this camera',
        'type': 'choices',
        'choices': IMXFX_CHOICES,
        'section': 'device',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_raspimjpeg_settings,
        'set': _set_raspimjpeg_settings,
        'get_set_dict': True
    }


@additional_config
def sePort():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Streaming Port',
        'description': 'sets the TCP port on which the webcam streaming server listens',
        'type': 'number',
        'min': 1024,
        'max': 65535,
        'section': 'streaming',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_nginx_settings,
        'set': _set_nginx_settings,
        'get_set_dict': True
    }


@additional_config
def seAuthMode():
    if not _get_stream_eye_enabled():
        return None

    return {
        'label': 'Authentication Mode',
        'description': 'the authentication mode to use when accessing the stream (use Basic instead of Digest if you encounter issues with third party apps)',
        'type': 'choices',
        'choices': AUTH_CHOICES,
        'section': 'streaming',
        'advanced': True,
        'camera': True,
        'required': True,
        'get': _get_nginx_settings,
        'set': _set_nginx_settings,
        'get_set_dict': True
    }
