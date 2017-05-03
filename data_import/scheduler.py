import threading
import random
import shutil
import time
import sys
import os
from calendar import monthrange
import MySQLdb as sql

sys.path.insert(0,'..')
import search
import download
import preproc
from dataImportUtils import(
	check_scene_exists,
	DownloadStatus,
	PreProcStatus,
	cleanup,
	random_date
)
from settings import(
	CLOUD_MAX,
	DAY_DELAY,
	DOWNLOAD_TIMEOUT
)
import settings
import schedulerIO
from utils import (
	check_create_folder,
	generateFilePathStr
)



class Task:
	def __init__(self, id):
		self.id = id
		self.status = 'PENDING'
		self.history = []


	def updateStatus(self, obj):
		self.history.append(self.status)
		self.status = obj
		return(0)



class Scheduler:
	def __init__(self):
		#check-installation
		check_create_folder(generateFilePathStr())
		check_create_folder(generateFilePathStr(type = 'raw'))
		check_create_folder(generateFilePathStr(type = 'preproc'))
		check_create_folder(generateFilePathStr(type = 'preproc', file = 'visible'))

		#initialize
		self.pausedT = False
		self.pausedSearchT = False
		self.pausedDownloadT = False
		self.pausedExtractT = False
		self.shutdownT = False
		self.shutdownSearchT = False
		self.shutdownDownloadT = False
		self.shutdownExtractT = False
		self.log = []

		self.db = sql.connect(
			db=settings.DB,
			host=settings.DB_HOST,
			user=settings.DB_USER,
			passwd=settings.DB_PASS
		)
		self.cur = self.db.cursor()

		self.s = search.Search()
		self.d = download.Downloader()
		self.p = preproc.Preprocessor()

		self.d_queue_auto = []
		self.d_queue_man = []
		self.p_queue = []

		self.addLog('scheduler started')
		threading.Thread(target = self.autoSched).start()
		threading.Thread(target = self.downloadHandler).start()
		threading.Thread(target = self.preprocHandler).start()
		schedulerIO.SchedulerIO(self)


	def getSearch(self, start, stop):
		results1 = self.s.search(
			start_date=start, end_date=stop,
			cloud_max = CLOUD_MAX,
			limit = 2000
		)

		if (results1['status'] == u'SUCCESS'):
			if (results1['total_returned'] == 2000):
				self.addLog('WARNING: search result overflow')
			return(results1['results'])
		return(1)


	def autoSched(self):
		month = None
		year = None
		while not self.shutdownT:
			if not self.pausedT and not self.pausedSearchT and len(self.d_queue_auto) < 20:
				try:
					datestr = random_date()
					searchResult = [str(i['sceneID']) for i in self.getSearch(datestr, datestr)]
					if len(searchResult) > 0:
						searchResult = random.choice(searchResult)
						self.d_queue_auto.append(Task(searchResult))
						self.addLog('queue updated with scene {0}'.format(searchResult))
				except Exception as e:
					self.addLog('queue update failed: {0}'.format(e))
			time.sleep(30)
		self.shutdownSearchT = True
		return(0)


	def insertJob(self, sceneid):
		if not check_scene_exists(sceneid, self.db, self.cur):
			self.d_queue_man.append(Task(sceneid))
			self.addLog('scene {0} inserted into manual queue'.format(sceneid))
			return(0)
		self.addLog('scene {0} already in image index'.format(sceneid))
		return(1)


	def downloadHelper(self, x):
		if check_scene_exists(x.id, self.db, self.cur):
			self.addLog('scene {0} already in index, continuing'.format(x.id))
			return(0)

		x.updateStatus(DownloadStatus())
		self.addLog('downloading scene: {0}'.format(x.id))

		n = DOWNLOAD_TIMEOUT
		while n > 0 and not self.pausedDownloadT:
			try:
				code = self.d.download(x.id, x.status)
				if code != 0: raise Exception
				self.p_queue.append(x)
				self.addLog('scene {0} downloaded, added to processing queue'.format(x.id))
				x.status = 'PENDING'
				break
			except:
				self.addLog('scene {0} download failure, attempts remaining: ({1}/{2})'.format(x.id, n, DOWNLOAD_TIMEOUT))
				x.status.reset()
				shutil.rmtree(generateFilePathStr(x.id, 'raw'))
				n -= 1
				time.sleep(2)

		if n == 0:
			self.addLog('scene {0} download failure timeout, aborting'.format(x.id))


	def downloadHandler(self):
		while not self.shutdownT:
			if len(self.d_queue_man) > 0 and not self.pausedT and not self.pausedDownloadT:
				x = self.d_queue_man[0]
				self.downloadHelper(x)
				del self.d_queue_man[0]

			elif len(self.d_queue_auto) > 0 and not self.pausedT and not self.pausedDownloadT:
				x = self.d_queue_auto[0]
				self.downloadHelper(x)
				del self.d_queue_auto[0]

			time.sleep(5)

		self.shutdownDownloadT = True
		return(0)


	def preprocHandler(self):
		while (not self.shutdownT) or (not self.shutdownDownloadT):
			if len(self.p_queue) > 0 and not self.pausedT and not self.pausedExtractT:
				x = self.p_queue[0]
				self.addLog('processing scene: {0}'.format(x.id))

				if check_scene_exists(x.id, self.db, self.cur):
					del self.p_queue[0]
					self.addLog('scene {0} already in index, continuing'.format(x.id))
					continue

				x.updateStatus(PreProcStatus())
				message = self.p.preproc(x.id, x.status)
				if message == 0: self.addLog('scene {0} processed'.format(x.id))
				else: self.addLog('scene {0} processing failed'.format(x.id))
				del self.p_queue[0]
			time.sleep(5)

		self.shutdownExtractT = True
		return(0)


	def addLog(self, str):
		self.log.append(time.strftime("[%H:%M:%S] ", time.localtime()) + 'SCHEDULER: ' + str)
		return(0)


	def shutdown(self):
		self.addLog('shutting down scheduler...')
		self.shutdownT = True
		self.addLog('waiting for threads to complete tasks (this can take a while)...')

		start = time.time()
		while not self.shutdownSearchT or not self.shutdownExtractT or not self.shutdownDownloadT:
			if time.time() - start > 1800:
				self.addLog('WARNING: threads have failed to close, manually closing critical resources')
				self.addLog('WARNING: wait for completion before termination - database may need cleanup after shutdown')
				break
			time.sleep(10)

		# shutdown preproc
		cleanup(self.p.db, self.p.cur, self.p.h5F)
		self.p.h5F.close()
		self.p.cur.close()
		self.p.db.close()

		# shutdown self
		self.cur.close()
		self.db.close()

		self.addLog('resources closed')
		return(0)


Scheduler()