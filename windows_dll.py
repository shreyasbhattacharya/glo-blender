"""Inspect Windows imports without loading DLLs into Blender."""
import os
from pathlib import Path
import struct

# OS-provided libraries used by the bundled tools. VC/OpenMP runtimes are
# deliberately not in this list: they must ship with the Windows release.
SYSTEM_DLLS = set('''
advapi32.dll authz.dll avicap32.dll avrt.dll bcrypt.dll bcryptprimitives.dll
cfgmgr32.dll comdlg32.dll crypt32.dll d2d1.dll d3d11.dll d3d12.dll d3d9.dll d3dcompiler_47.dll
dbghelp.dll dnsapi.dll dwrite.dll dwmapi.dll dxgi.dll dxva2.dll gdi32.dll
imm32.dll iphlpapi.dll kernel32.dll kernelbase.dll mf.dll mfplat.dll
mfreadwrite.dll mfuuid.dll mpr.dll msimg32.dll msvcrt.dll ncrypt.dll netapi32.dll
normaliz.dll ntdll.dll ole32.dll oleaut32.dll opengl32.dll powrprof.dll
propsys.dll psapi.dll rpcrt4.dll secur32.dll setupapi.dll shcore.dll
shell32.dll shlwapi.dll user32.dll userenv.dll uxtheme.dll ucrtbase.dll
version.dll winhttp.dll winmm.dll winspool.drv wintrust.dll ws2_32.dll
wtsapi32.dll uiautomationcore.dll
'''.split())


def is_system_dll(name):
    return name.lower() in SYSTEM_DLLS or name.lower().startswith(('api-ms-win-', 'ext-ms-win-'))


def pe_imports(data):
    """Return machine and load-time import names for a PE32/PE32+ image."""
    def unpack(fmt, offset):
        try:
            return struct.unpack_from(fmt, data, offset)
        except struct.error as exc:
            raise ValueError('Truncated Windows executable') from exc

    if data[:2] != b'MZ':
        raise ValueError('Not a Windows executable')
    pe, = unpack('<I', 60)
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Invalid PE signature')
    machine, count = unpack('<HH', pe + 4)
    optional_size, = unpack('<H', pe + 20)
    optional = pe + 24
    magic, = unpack('<H', optional)
    if magic not in (0x10B, 0x20B):
        raise ValueError('Unsupported PE optional header')
    directory = optional + (112 if magic == 0x20B else 96)
    directory_count, = unpack('<I', directory - 4)
    if directory_count < 2:
        return machine, []
    imports, size = unpack('<II', directory + 8)
    if not imports or not size:
        return machine, []
    sections = []
    for index in range(count):
        section = optional + optional_size + index * 40
        virtual_size, address, raw_size, raw = unpack('<IIII', section + 8)
        sections.append((address, max(virtual_size, raw_size), raw, raw_size))

    def offset(rva):
        for address, length, raw, raw_size in sections:
            if address <= rva < address + length and rva - address < raw_size:
                result = raw + rva - address
                if result < len(data):
                    return result
        raise ValueError(f'Invalid PE RVA: {rva:#x}')

    names = []
    for index in range(size // 20):
        descriptor = unpack('<IIIII', offset(imports + index * 20))
        if not any(descriptor):
            return machine, names
        start = offset(descriptor[3])
        end = data.find(b'\0', start, start + 512)
        if end < 0:
            raise ValueError('Invalid PE import name')
        names.append(data[start:end].decode('ascii').lower())
    raise ValueError('Unterminated PE import table')


def dependency_report(executable, env):
    """Diagnose the child process search path, not Blender's loaded modules.

    API-set DLL names are virtual OS contracts, not necessarily disk files.
    System DLL internals are excluded from this application dependency walk.
    """
    executable = Path(executable).resolve()
    windows = Path(env.get('SystemRoot') or env.get('SYSTEMROOT', r'C:\Windows'))
    system_dirs = [windows/'System32', windows]
    directories = [executable.parent, *system_dirs, Path.cwd(),
                   *(Path(p) for p in env.get('PATH', '').split(os.pathsep) if p)]
    available = {}
    for directory in directories:
        try:
            for path in directory.iterdir():
                if path.suffix.lower() in ('.dll', '.drv'):
                    available.setdefault(path.name.lower(), path)
        except OSError:
            continue
    lines = [f'Executable: {executable}', f'Working directory: {Path.cwd()}',
             'DLL search directories:', *(f'  {p}' for p in directories)]
    missing = set()
    seen = set()
    pending = [executable]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            machine, imports = pe_imports(path.read_bytes())
            if machine != 0x8664:
                lines.append(f'Wrong architecture: {path} (machine {machine:#x}, expected x64)')
            for name in imports:
                if name.startswith(('api-ms-win-', 'ext-ms-win-')):
                    continue
                dependency = available.get(name)
                if dependency is None:
                    missing.add(name)
                    lines.append(f'MISSING: {name} (required by {path.name})')
                else:
                    lines.append(f'{path.name} -> {name}: {dependency}')
                    if dependency.parent not in system_dirs:
                        pending.append(dependency)
        except (OSError, ValueError) as exc:
            lines.append(f'Cannot inspect {path}: {exc}')
    lines.append('Missing DLLs: ' + (', '.join(sorted(missing)) or
                 'none found in static imports; inspect Windows loader/runtime compatibility'))
    return lines, sorted(missing)
