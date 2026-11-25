"""Exercise the settings, authenticated request and selection refresh with synthetic data."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import tkinter as tk
import ai_dialog
import credentials
import main
import music_search
from verify_import import Replica


def check_settings(root):
    with patch.object(credentials, 'NAME', 'NanoDrop-Tests/' + uuid4().hex):
        try:
            initial = credentials.read_settings()
            assert not initial.key
            def create(dialog):
                dialog.address.set('https://catalog.example/v1')
                dialog.model.set('test-model')
                dialog.secret.set('first-test-key')
            def replace(dialog):
                assert dialog.secret.get() == ''
                dialog.secret.set('second-test-key')
            def resave(dialog):
                dialog.model.set('updated-model')

            for change in (create, replace, resave):
                def submit(change=change):
                    dialog = next(w for w in root.winfo_children() if isinstance(w, ai_dialog.AISettingsDialog))
                    change(dialog)
                    dialog.ok()
                root.after(60, submit)
                assert ai_dialog.AISettingsDialog(root, credentials.read_settings()).result

            profile = credentials.read_settings()
            assert profile.model == 'updated-model' and profile.key == 'second-test-key'
            for base in ('http://example.org', 'https://elsewhere.example/v1', 'https://a:b@example.org',
                         'https://example.org/?token=test'):
                try:
                    credentials.settings(base, 'model', '', profile)
                except ValueError:
                    pass
                else:
                    raise AssertionError('invalid address or accidental key reuse')

            response = {'choices': [{'message': {'content': '{"ranked": []}'}}]}
            opener = Mock()
            opener.open.return_value = io.BytesIO(json.dumps(response).encode())
            track = SimpleNamespace(title='Demo', artist='Example', album='QA', length_ms=1500)
            with patch.object(music_search, 'build_opener', return_value=opener) as factory:
                music_search.advice(track, [], profile, music_search.SearchRequest('title', 'Demo'))
            request = opener.open.call_args.args[0]
            assert request.full_url == 'https://catalog.example/v1/chat/completions'
            assert request.get_header('Authorization') == 'Bearer ' + profile.key
            assert json.loads(request.data)['model'] == profile.model
            assert isinstance(factory.call_args.args[0], music_search.NoRedirect)
            assert factory.call_args.args[0].redirect_request(None, None, None, None, None, None) is None
            with patch.object(ai_dialog.messagebox, 'askyesno', return_value=True):
                root.after(60, lambda: next(w for w in root.winfo_children()
                           if isinstance(w, ai_dialog.AISettingsDialog)).clear())
                assert ai_dialog.AISettingsDialog(root, profile).result
            assert credentials.read_settings() == initial
        finally:
            credentials.clear_settings()


def check_selection(root):
    view = main.Window(root)
    device = Replica(Path('synthetic-device'), bytes(range(8)))
    tracks = [SimpleNamespace(track_id=i, title=f'Song {i}', artist='', album='', length_ms=1000,
              ipod=None, metadata=SimpleNamespace(location=f'{i}.mp3')) for i in (1, 2)]
    def scan_result(dev, songs):
        stats = ('QA', songs, 1024, {str(t.track_id): (12, '12 B') for t in songs})
        view.events.put(('scan', (dev, True, None, stats if dev else None)))
        view.drain()
        root.update_idletasks()
    with patch.object(view, 'worker'), patch.object(view, 'begin_search') as start:
        scan_result(device, tracks)
        view.tabs.select(1)
        view.catalog.selection_set('1')
        view.catalog.focus('1')
        view.selected_tracks()
        view.scan()
        assert str(view.search_btn['state']) == 'normal'
        view.search_btn.invoke()
        view.search_btn.invoke()
        start.assert_not_called()
        scan_result(device, tracks)
        start.assert_called_once()
        assert start.call_args.args[1] is tracks[0] and view.catalog.selection() == ('1',)
        assert view.catalog.focus() == '1'
        start.reset_mock()
        view.scan()
        view.search_btn.invoke()
        view.catalog.selection_set('2')
        scan_result(device, tracks)
        start.assert_not_called()
        view.catalog.selection_set('1', '2')
        view.selected_tracks()
        assert str(view.search_btn['state']) == 'disabled'
        view.catalog.selection_set('1')
        view.selected_tracks()
        view.scan()
        view.search_btn.invoke()
        scan_result(device, tracks[1:])
        assert not view.catalog.selection()
        start.assert_not_called()
        view.catalog.selection_set('2')
        view.selected_tracks()
        view.scan()
        view.search_btn.invoke()
        scan_result(None, [])
        assert str(view.search_btn['state']) == 'disabled'
        start.assert_not_called()


if __name__ == '__main__':
    root = main.TkinterDnD.Tk()
    root.withdraw()
    try:
        check_settings(root)
        check_selection(root)
    finally:
        root.destroy()
    print('Passed: isolated credential manager, dialog, custom API routing, selection and refresh.')
