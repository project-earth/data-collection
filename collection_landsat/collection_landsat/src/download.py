import os
import requests

from lib_core.path_resolution.path_resolution import generate_file_path, check_create_folder
from collection_landsat.src.data_import_utils import (
	url_builder,
	remote_file_exists,
	scene_interpreter
)


class Downloader(object):
	def __init__(self, data_dir, base_api_url, logger):
		self.logger = logger
		self.data_dir = data_dir
		self.google_api_url = base_api_url


	def download(self, sceneid):
		interpScene = scene_interpreter(sceneid)
		url = self.get_url(interpScene)
		check = remote_file_exists(url)

		if remote_file_exists(url):
			check_create_folder(generate_file_path(self.data_dir, sceneid, 'raw'))
			r = requests.get(url, stream=True, timeout=5)
			f = open(generate_file_path(self.data_dir, sceneid, 'raw', 'tar'), 'wb')
			for chunk in r.iter_content(chunk_size=2048):
				if chunk:
					f.write(chunk)
			f.close()

		else:
			raise Exception("scene {} was not found on the remote server".format(sceneid))


	def get_url(self, sat):
		""" gets google download url given an interpreted scene ID
		"""
		filename = sat['scene'] + '.tar.bz'
		return url_builder([self.google_api_url, sat['sat'], sat['path'], sat['row'], filename])
