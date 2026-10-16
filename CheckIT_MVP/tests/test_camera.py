import base64
import unittest
from unittest.mock import patch

from camera import usb_device, usb_photo


class CameraTests(unittest.TestCase):
    def test_device_selection_and_connection_errors(self):
        device = {'id': 'usb-1', 'label': 'USB CAMERA'}
        with patch('camera._camera', return_value={'device': device, 'generation': 0}):
            self.assertEqual(usb_device(), device)
        with patch('camera._camera', return_value={'error': 'USB disconnected'}), patch('camera.st.error') as error:
            self.assertIsNone(usb_device())
            error.assert_called_once()

    def test_device_waits_for_current_connection_generation(self):
        with patch('camera._camera', return_value={'device': {'id': 'usb-1', 'label': 'USB CAMERA'}, 'generation': -1}):
            self.assertIsNone(usb_device())

    def test_photo_uses_the_selected_device(self):
        device = {'id': 'usb-1', 'label': 'USB CAMERA'}
        result = {'device_id': 'usb-1', 'photo': base64.b64encode(b'photo').decode('ascii')}
        with patch('camera._camera', return_value=result) as component:
            self.assertEqual(usb_photo(device, 'photo').getvalue(), b'photo')
            self.assertEqual(component.call_args.kwargs['device'], device)
            self.assertEqual(component.call_args.kwargs['mode'], 'photo')

    def test_photo_from_previous_device_is_not_reused(self):
        with patch('camera._camera', return_value={'device_id': 'old-usb', 'photo': 'cGhvdG8='}):
            self.assertIsNone(usb_photo({'id': 'new-usb', 'label': 'USB CAMERA'}, 'photo'))

    def test_invalid_photo_is_reported(self):
        with patch('camera._camera', return_value={'device_id': 'usb-1', 'photo': 'invalid!'}), patch('camera.st.error') as error:
            self.assertIsNone(usb_photo({'id': 'usb-1', 'label': 'USB CAMERA'}, 'photo'))
            error.assert_called_once()
