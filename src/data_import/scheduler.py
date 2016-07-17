import threading
import random
import datetime
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
	DownloadStatus,
	PreProcStatus
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



def checkScene(sceneid, db, cur):
	cur.execute("SELECT EXISTS (SELECT 1/0 FROM imageindex WHERE lid='{0}');".format(sceneid))
	return(cur.fetchall()[0][0] == 1)



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
		self.pausedDownloadT = False
		self.pausedExtractT = False
		self.shutdownT = False
		self.shutdownDownloadT = False
		self.shutdownExtractT = False
		self.log = []

		self.db = sql.connect(
			db="earthdat",
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
		threading.Thread(target = self.monthlyAutoSched).start()
		threading.Thread(target = self.downloadHandler).start()
		threading.Thread(target = self.preprocHandler).start()
		schedulerIO.SchedulerIO(self)


	def getSearch(self, mon, yr):
		nDays = monthrange(yr, mon)[1]

		results1 = self.s.search(
			start_date = '{0}-{1}-01'.format(yr, mon),
			end_date = '{0}-{1}-06'.format(yr, mon, nDays),
			cloud_max = CLOUD_MAX,
			limit = 4000
		)
		results2 = self.s.search(
			start_date = '{0}-{1}-07'.format(yr, mon),
			end_date = '{0}-{1}-12'.format(yr, mon, nDays),
			cloud_max = CLOUD_MAX,
			limit = 4000
		)
		results3 = self.s.search(
			start_date = '{0}-{1}-13'.format(yr, mon),
			end_date = '{0}-{1}-18'.format(yr, mon, nDays),
			cloud_max = CLOUD_MAX,
			limit = 4000
		)
		results4 = self.s.search(
			start_date = '{0}-{1}-19'.format(yr, mon),
			end_date = '{0}-{1}-24'.format(yr, mon, nDays),
			cloud_max = CLOUD_MAX,
			limit = 4000
		)
		results5 = self.s.search(
			start_date = '{0}-{1}-25'.format(yr, mon),
			end_date = '{0}-{1}-{2}'.format(yr, mon, nDays),
			cloud_max = CLOUD_MAX,
			limit = 4000
		)

		if (
			results1['status'] == u'SUCCESS' and
			results2['status'] == u'SUCCESS' and
			results3['status'] == u'SUCCESS' and
			results4['status'] == u'SUCCESS' and
			results5['status'] == u'SUCCESS'
		):
			if (
				results1['total_returned'] == 4000 or
				results2['total_returned'] == 4000 or
				results3['total_returned'] == 4000 or 
				results4['total_returned'] == 4000 or 
				results5['total_returned'] == 4000
			):
				self.addLog('WARNING: search result overflow')
			return(results1['results'] + results2['results'] + results3['results'])

		return(1)


	def monthlyAutoSched(self):
		month = None
		year = None
		while not self.shutdownT:
			searchTime = datetime.datetime.now() - datetime.timedelta(days=DAY_DELAY)
			if searchTime.month != month and not self.pausedT:
				self.addLog('updating queue for new month ...')
				self.d_queue_auto = []
				try:
					monthTemp = searchTime.month
					yearTemp = searchTime.year
					searchResults = [str(i['sceneID']) for i in self.getSearch(monthTemp, yearTemp)]
					month = monthTemp
					year = yearTemp
					random.shuffle(searchResults)
					self.d_queue_auto = [Task(i) for i in searchResults if not checkScene(i, self.db, self.cur)]
					self.addLog('queue updated with {0} entries'.format(len(searchResults)))
				except:
					self.addLog('queue update failed')
			time.sleep(30)
		return(0)


	def downloadHandler(self):
		while not self.shutdownT:
			if len(self.d_queue_man) > 0 and not self.pausedT and not self.pausedDownloadT:
				x = self.d_queue_man[0]
				x.updateStatus(DownloadStatus())
				self.addLog('downloading scene: {0}'.format(x.id))

				n = DOWNLOAD_TIMEOUT
				while n > 0 and not self.pausedDownloadT:
					try:
						self.d.download(x.id, x.status)
						self.p_queue.append(x)
						self.addLog('scene {0} downloaded, added to processing queue'.format(x.id))
						x.status = 'PENDING'
						del self.d_queue_man[0]
						break
					except:
						x.status.failed()
						self.addLog('scene {0} download failure, attempts remaining: ({1}/{2})'.format(x.id, n, DOWNLOAD_TIMEOUT))
						n -= 1
				if n == 0:
					self.addLog('scene {0} download failure timeout, aborting'.format(x.id))
					del self.d_queue_man[0]

			elif len(self.d_queue_auto) > 0 and not self.pausedT:
				x = self.d_queue_auto[0]
				x.updateStatus(DownloadStatus())
				self.addLog('downloading scene: {0}'.format(x.id))

				n = DOWNLOAD_TIMEOUT
				while n > 0 and not self.pausedDownloadT:
					try:
						self.d.download(x.id, x.status)
						self.p_queue.append(x)
						self.addLog('scene {0} downloaded, added to processing queue'.format(x.id))
						x.status = 'PENDING'
						del self.d_queue_auto[0]
						break
					except:
						x.status.failed()
						self.addLog('scene {0} download failure, attempts remaining: ({1}/{2})'.format(x.id, n, DOWNLOAD_TIMEOUT))
						n -= 1
				if n == 0:
					self.addLog('scene {0} download failure timeout, aborting'.format(x.id))
					del self.d_queue_auto[0]

			time.sleep(5)

		self.shutdownDownloadT = True
		return(0)


	def preprocHandler(self):
		while (not self.shutdownT) or (not self.shutdownDownloadT):
			if len(self.p_queue) > 0 and not self.pausedT and not self.pausedExtractT:
				x = self.p_queue[0]
				self.addLog('processing scene: {0}'.format(x.id))
				x.updateStatus(PreProcStatus())
				message = self.p.preproc(x.id, x.status)
				if message == 0: self.addLog('scene {0} processing'.format(x.id))
				else: self.addLog('scene {0} processing failed'.format(x.id))
				del self.p_queue[0]
			time.sleep(5)

		# Deletes unprocessed downloads
		for task in self.p_queue:
			os.remove(generateFilePathStr(task.id, 'raw', 'tar'))
			os.remove(generateFilePathStr(task.id, 'raw'))

		self.shutdownExtractT = True
		return(0)


	def addLog(self, str):
		self.log.append(time.strftime("[%H:%M:%S] ", time.localtime()) + 'SCHEDULER: ' + str)
		return(0)


	def shutdown(self):
		self.addLog('shutting down scheduler...')
		self.shutdownT = True
		self.addLog('waiting for threads to complete tasks (this can take a while)...')
		return(0)


Scheduler()
