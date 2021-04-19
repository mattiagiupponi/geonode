from io import BufferedReader, IOBase
import os
from gisdata import GOOD_DATA
import requests
from requests.models import HTTPBasicAuth

_file = os.path.join(GOOD_DATA, "raster", "relief_san_andres.tif")

spatial_files = ("dbf_file", "shx_file", "prj_file")
base, ext = os.path.splitext(_file)
params = {
    # make public since wms client doesn't do authentication
    'permissions': '{ "users": {"AnonymousUser": ["view_resourcebase"]} , "groups":{}}',
    "time": "false",
    "layer_title": "relief_san_andres.tif",
    "abstract": "",
    "time": "false",
    "charset": "UTF-8",
}
print("deal with shapefiles")
if ext.lower() == ".shp":
    for spatial_file in spatial_files:
        ext, _ = spatial_file.split("_")
        file_path = f"{base}.{ext}"
        # sometimes a shapefile is missing an extra file,
        # allow for that
        if os.path.exists(file_path):
            params[spatial_file] = open(file_path, "rb")
elif ext.lower() == ".tif":
    file_path = base + ext
    params["tif_file"] = open(file_path, "rb")

files = {}

client = requests.session()

with open(_file, "rb") as base_file:
    params["base_file"] = base_file
    for name, value in params.items():
        if isinstance(value, BufferedReader):
            files[name] = (os.path.basename(value.name), value)
            params[name] = (os.path.basename(value.name))

    # calling geonode for create import task
    print("Sending PUT request")
    response = client.put(
        f"http://localhost:8000/api/v2/uploads/upload/", auth=HTTPBasicAuth("admin", "admin"), data=params, files=files
    )
    
    print(response.status_code)

print("Closing spatial files")
# Closes the files
for spatial_file in spatial_files:
    if isinstance(params.get(spatial_file), IOBase):
        params[spatial_file].close()

if isinstance(params.get("tif_file"), IOBase):
    params['tif_file'].close()

print("getting import_id")
import_id = int(response.json()['redirect_to'].split('?id=')[1])
print(f"ImportID found {import_id}")

print(f"getting upload_list")
y = client.get('http://localhost:8000/api/v2/uploads/', auth=HTTPBasicAuth("admin", "admin"))

print(f"extraction of upload_id")

upload_id = None
for item in y.json()['uploads']:
    if item.get('import_id', None) == import_id:
        upload_id = item.get('id', None)
        break

print(f"UploadID found {upload_id}")

print(f"Calling upload detail page")
client.get(f'http://localhost:8000/api/v2/uploads/{upload_id}', auth=HTTPBasicAuth("admin", "admin"))

print(f"Calling final upload page")
final = client.get(f'http://localhost:8000/upload/final?id={import_id}', auth=HTTPBasicAuth("admin", "admin"))

print(f'http://localhost:8000/upload/final?id={import_id}')
print(final.text)

# TODO: add loop for check the status. Must be on PROCESSED to be completed at 100%