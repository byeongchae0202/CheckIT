"""배포 허용 파일만 ZIP으로 만들고 사용자 데이터·실제 인증키는 제외한다."""
import argparse
from pathlib import Path
import zipfile


def package(project, destination):
    project, destination = Path(project).resolve(), Path(destination).resolve()
    core = ('app.py', 'arduino_controller.py', 'checklist.py', 'config.py', 'detector.py',
            'profiles.py', 'realtime.py', 'regions.py', 'weather.py', 'personal.py',
            'personal_ui.py', 'local_settings.example.py')
    paths = [project / name for name in core]
    paths += [project / name for name in ('README.md', 'UI_DESIGN.md', 'VALIDATION.md', 'requirements.txt', '.gitignore')]
    for directory, extensions in [('tests', {'.py'}), ('scripts', {'.py'}), ('arduino', {'.ino', '.md'}), ('.streamlit', {'.toml'})]:
        paths += [path for path in (project / directory).rglob('*') if path.is_file() and path.suffix in extensions and '__pycache__' not in path.parts]
    paths += [project / 'data' / name for name in ('items.json', 'weather_regions.json')]
    temporary = destination.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            archive.write(path, 'CheckIT_MVP/' + path.relative_to(project).as_posix())
        # 사용자 설정은 읽지 않고 안전한 예제 파일로 대체한다.
        archive.writestr('CheckIT_MVP/local_settings.py', (project / 'local_settings.example.py').read_bytes())
    with zipfile.ZipFile(temporary) as archive:
        names = archive.namelist()
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC 검사 실패')
        if archive.read('CheckIT_MVP/local_settings.py') != (project / 'local_settings.example.py').read_bytes():
            raise ValueError('설정 파일 검사 실패')
        for name in names:
            if name.endswith(('.sqlite3', '.pt', '.pyc')) or '__pycache__' in name:
                raise ValueError('사용자 데이터 또는 런타임 파일이 포함됐습니다.')
            if name.endswith('.py'):
                compile(archive.read(name).decode('utf-8-sig'), name, 'exec')
    temporary.replace(destination)
    report = destination.with_name(destination.stem + '_zip_validation.txt')
    report.write_text('ZIP: ' + str(destination) + '\nFiles: ' + str(len(names)) +
                      '\nRequired files: OK\nCRC: OK\nPython syntax: OK\nPrivate settings: placeholder only\nUser DB/photos/model: excluded\n\n' + '\n'.join(sorted(names)) + '\n', encoding='utf-8')
    print('RELEASE_OK', len(names), 'files')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('project')
    parser.add_argument('destination')
    args = parser.parse_args()
    package(args.project, args.destination)
