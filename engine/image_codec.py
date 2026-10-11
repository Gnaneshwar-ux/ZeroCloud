"""Load common images normally; use installed Windows codecs if HEIF is blocked."""
import os,subprocess,tempfile
from pathlib import Path
from PIL import Image

def open_image(source):
    source=Path(source)
    if source.suffix.lower() not in ('.heic','.heif'):return Image.open(source)
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        return Image.open(source)
    except (ImportError,OSError):
        if os.name!='nt':raise
    # Signed Windows imaging components, not a relaxed execution/signing policy.
    command="""Add-Type -AssemblyName PresentationCore; $stream=[System.IO.File]::OpenRead($env:ZEROCLOUD_CODEC_SOURCE); try {$decoder=[System.Windows.Media.Imaging.BitmapDecoder]::Create($stream,[System.Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat,[System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad); $encoder=New-Object System.Windows.Media.Imaging.PngBitmapEncoder; $encoder.Frames.Add($decoder.Frames[0]); $out=[System.IO.File]::Create($env:ZEROCLOUD_CODEC_OUTPUT); try {$encoder.Save($out)} finally {$out.Dispose()}} finally {$stream.Dispose()}"""
    with tempfile.TemporaryDirectory(prefix='zerocloud-codec-') as temporary:
        output=Path(temporary)/'frame.png'
        env={**os.environ,'ZEROCLOUD_CODEC_SOURCE':str(source.resolve()),'ZEROCLOUD_CODEC_OUTPUT':str(output)}
        executable=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result=subprocess.run([str(executable),'-NoProfile','-NonInteractive','-Command',command],env=env,stdin=subprocess.DEVNULL,capture_output=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or not output.is_file():raise OSError('Windows could not decode this HEIF image. Check the installed HEIF/HEVC image extensions.')
        with Image.open(output) as decoded:image=decoded.copy()
    import exifread
    with source.open('rb') as stream:tags=exifread.process_file(stream,details=False,extract_thumbnail=False)
    orientation=tags.get('Image Orientation')
    if orientation:
        exif=Image.Exif();exif[274]=int(orientation.values[0]);image.info['exif']=exif.tobytes()
    return image

def read_gps(source):
    import exifread,math
    with open(source,'rb') as stream:tags=exifread.process_file(stream,details=False,extract_thumbnail=False)
    def coordinate(key):
        values=tags[key].values
        return float(values[0])+float(values[1])/60+float(values[2])/3600
    if 'GPS GPSLatitude' not in tags or 'GPS GPSLongitude' not in tags:return None,None
    lat=coordinate('GPS GPSLatitude')*(-1 if str(tags.get('GPS GPSLatitudeRef'))=='S' else 1)
    lon=coordinate('GPS GPSLongitude')*(-1 if str(tags.get('GPS GPSLongitudeRef'))=='W' else 1)
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('Invalid GPS coordinates')
    return lat,lon
