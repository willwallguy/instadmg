#!/usr/bin/python

# InstaUpToDate
#
#	This script parses one or more catalog files to fill in the 

import os, sys, re
import hashlib, urlparse, urllib, urllib2, tempfile, shutil, subprocess, datetime, atexit
import checksum # this is part of the instadmg suite

#------------------------------SETTINGS------------------------------

svnRevision					= int('$Revision$'.split(" ")[1])
versionString				= "0.5b (svn revision: %i)" % svnRevision

allowedCatalogFileSettings	= [ "ISO Language Code", "Output Volume Name", "Output File Name" ]

# these should be in the order they run in
systemSectionTypes			= [ "OS Updates", "System Settings" ]
addedSectionTypes			= [ "Apple Updates", "Third Party Software", "Third Party Settings", "Software Settings" ]	

#------------------------RUNTIME ADJUSTMENTS-------------------------

absPathToInstaDMGFolder		= os.path.normpath(os.path.join( os.path.abspath(os.path.dirname(sys.argv[0])), "../../" ))

appleUpdatesFolderPath		= os.path.normpath(os.path.join(absPathToInstaDMGFolder, "InstallerFiles", "BaseUpdates"))
customPKGFolderPath			= os.path.normpath(os.path.join(absPathToInstaDMGFolder, "InstallerFiles", "CustomPKG"))
userSuppliedPKGFolderPath	= os.path.normpath(os.path.join(absPathToInstaDMGFolder, "InstallerFiles", "InstaUp2DatePackages"))
baseOSFolderPath			= os.path.normpath(os.path.join(absPathToInstaDMGFolder, "InstallerFiles", "Base OS Disk"))

cacheFolderPath				= os.path.normpath(os.path.join(absPathToInstaDMGFolder, "Caches", "InstaUp2DateCache"))

#-------------------------------CLASSES------------------------------

class CatalogNotFoundException(Exception):
	pass

class FileNotFoundException(Exception):
	pass

class instaUpToDate:
	"The central class to manage the process"
		
	#---------------------Class Variables-----------------------------
	
	sectionStartParser		= re.compile('^(?P<sectionName>[^\t]+):\s*(#.*)?$')
	packageLineParser		= re.compile('^\t(?P<displayName>[^\t]*)\t(?P<fileLocation>[^\t]+)\t(?P<fileChecksum>\S+)\s*(#.*)?$')
	emptyLineParser			= re.compile('^\s*(?P<comment>#.*)?$')
	settingLineParser		= re.compile('^(?P<variableName>[^=]+) = (?P<variableValue>.*)')
	includeLineParser		= re.compile('^\s*include-file:\s+(?P<location>.*)(\s*#.*)?$')
	
	fileExtensions			= ['.catalog']
	
	#--------------------Instance Variables---------------------------
	
	sectionFolders			= None
	
	packageGroups 			= None	# a Hash, init-ed in cleanInstaDMGFolders
	parsedFiles 			= None	# an Array, for loop checking
	
	# defaults
	outputVolumeNameDefault = "MacintoshHD"
	
	# things below this line will usually come from the first catalog file (top) that sets them
	catalogFileSettings		= None	# a hash

	#---------------------Class Functions-----------------------------
	
	@classmethod
	def getCatalogFullPath(myClass, catalogFileInput, catalogFolders):
		'''Classmethod to translate input to a abs-path from one of the accepted formats (checked in this order):
	- ToDo: http or https reference (will be downloaded and temporary filepath returned)
	- absolute path to a file
	- catalog file name within the CatalogFiles folder, with or without the .catalog extension
	- relative path from CatalogFiles folder, with or without the .catalog extension
	- relative path from the pwd, with or without the .catalog extension
'''
		
		if catalogFolders is None:
			raise Exception('getCatalogFullPath was passed an emtpy catalogFolders')
			
		elif isinstance(catalogFolders, str) and os.path.isdir(str(catalogFolders)):
			catalogFolders = [catalogFolders]
		
		elif hasattr(catalogFolders, '__iter__'):
			for thisFolder in catalogFolders:
				if not os.path.isdir(str(thisFolder)):
					raise Exception('getCatalogFullPath was passed a bad catalog folder: ' + str(thisFolder))
			
		else:
			raise Exception('getCatalogFullPath unable to understand the catalogFolders given: ' + str(catalogFolders))
		
		
		# http/https url
		if urlparse.urlparse(catalogFileInput).scheme in ["http", "https"]:
			raise Exception("URL catalog files are not done yet")
			# ToDo: download the files, then return the path
		
		# try it as an absolute or relative file path
		if os.path.isfile(catalogFileInput):
			return os.path.abspath(os.path.realpath(catalogFileInput))
		
		# cycle through the folders we have been given to see if it is there
		for thisFolder in catalogFolders:
			
			# try the simple path:
			if os.path.isfile( os.path.join(thisFolder, catalogFileInput) ):
				return os.path.abspath(os.path.realpath(os.path.join(thisFolder, catalogFileInput)))
			
			# try appending file extension(s)
			for thisExtension in myClass.fileExtensions:
				if os.path.isfile( os.path.join(thisFolder, catalogFileInput + thisExtension) ):
					return os.path.abspath(os.path.realpath(os.path.join(thisFolder, catalogFileInput + thisExtension)))
		
		raise CatalogNotFoundException("The file input is not one that getCatalogFullPath understands, or can find: %s" % catalogFileInput)
		
	#------------------------Functions--------------------------------
	
	def __init__(self, sectionFolders, catalogFolders):
		
		# being a little paranoid... setting up section folders structure
		self.sectionFolders = []
		self.catalogFolders	= []
		self.packageGroups	= {}
		
		# catalogFolders
		if isinstance(catalogFolders, str) and os.path.isdir(catalogFolders):
			catalogFolders = [str(catalogFolders)]
		
		if hasattr(catalogFolders, '__iter__'):
			for thisCatalogFolder in catalogFolders:
				if not os.path.isdir(str(thisCatalogFolder)):
					raise Exception('%s called with a catalogFolder that was not a folder: %s' % (self.__class__, thisCatalogFolder))
				self.catalogFolders.append(str(thisCatalogFolder))
				
		else:
			raise Exception('%s called with a catalogFolder that could not be understood: %s' % (self.__class__, str(sectionFolders)))
		
		
		# sectionFolders
		if not hasattr(sectionFolders, '__iter__'):
			raise Exception('%s called with a sectionFolders attribute that was not an array: %s' % (self.__class__, sectionFolders))
		for thisFolder in sectionFolders:
			if not hasattr(thisFolder, 'has_key') or not thisFolder.has_key('folderPath') or not thisFolder.has_key('sections'):
				raise Exception('%s called with a sectionFolders that had a bad item in it: %s' % (self.__class__, thisFolder))
			
			newSection = {}
			
			if not isinstance(thisFolder['folderPath'], str) or not os.path.isdir(thisFolder['folderPath']):
				raise Exception('%s called with a sectionFolders that had a bad item in it (folderPath was not an existing path): %s' % (self.__class__, thisFolder))
			
			newSection['folderPath'] = str(thisFolder['folderPath'])
			
			if not hasattr(thisFolder['sections'], 'append'):
				raise Exception('%s called with a sectionFolders that had a bad item in it (sections was not an array): %s' % (self.__class__, thisFolder))
			
			newSection['sections'] = []
			
			for thisSectionName in thisFolder['sections']:
				if not str(thisSectionName) in (systemSectionTypes + addedSectionTypes):
					raise Exception('Section type not in allowed section types: ' + str(thisSectionName))
				
				for thisSectionFolder in self.sectionFolders:
					if str(thisSectionName) in thisSectionFolder['sections']:
						raise Exception('Section type was repeated: ' + str(thisSectionName))
					
					newSection['sections'].append(str(thisSectionName))
				
				self.packageGroups[str(thisSectionName)] = []
			
			self.sectionFolders.append(newSection)
		
		self.catalogFileSettings	= {}
		self.parsedFiles			= []
		
		# --- runtime checks ----
		assert os.path.isfile(os.path.join(absPathToInstaDMGFolder, "instadmg.bash")), "InstaDMG was not where it was expected to be: %s" % os.path.join(absPathToInstaDMGFolder, "instadmg.bash")
		assert os.path.isdir(userSuppliedPKGFolderPath), "The catalog files folder was not where it was expected to be: %s" % userSuppliedPKGFolderPath
		assert os.path.isdir(cacheFolderPath), "The instaDMG cache folder was not where it was expected to be: %s" % cacheFolderPath
	
	def parseFile(self, fileLocation):
		
		global allowedCatalogFileSettings
					
		# the file passed could be an absolute path, a relative path, or a catalog file name
		#	the first two are handled without a special section, but the name needs some work
		
		fileLocation = self.getCatalogFullPath(fileLocation, self.catalogFolders) # there should not be an error here, since we have already validated it
		# note: this last will have taken care of downloading any remote files
		
		assert os.path.isfile(fileLocation), "There was no file where it was expected to be: %s" % fileLocation
		
		# check to make sure we are not in a loop
		assert fileLocation not in self.parsedFiles, 'Loop detected in catalog files: %s' % fileLocation
		self.parsedFiles.append(fileLocation)
		
		inputfile = open(fileLocation, "r")
		if inputfile == None:
				raise Exception('Unable to open input file: %s' % inputFilePath) # TODO: improve error handling
			
		currentSection = None;
		lineNumber = 0
		
		# parse through the file
		for line in inputfile.readlines():
			lineNumber += 1
			
			if self.emptyLineParser.search(line):
				continue
			
			# ------- settings lines -------
			settingLineMatch = self.settingLineParser.search(line)
			if settingLineMatch:
				try:
					if allowedCatalogFileSettings.index( settingLineMatch.group("variableName") ):
						if not(self.catalogFileSettings.has_key( settingLineMatch.group("variableName") )):
							# Since it is not set, we can set it
							# TODO: log something if there is a conflict
							self.catalogFileSettings[settingLineMatch.group("variableName")] = settingLineMatch.group("variableValue")
				except:
					raise Exception('Unknown setting in catalog file: %s line number: %i\n%s' % (fileLocation, lineNumber, line)) # TODO: improve error handling
					
				continue
			
			# ----- file includes lines ----
			includeLineMatch = self.includeLineParser.search(line)
			if includeLineMatch:
				self.parseFile( self.getCatalogFullPath(includeLineMatch.group("location"), self.catalogFolders) )
				continue
			
			# ------- section lines --------
			sectionTitleMatch = self.sectionStartParser.search(line)
			if sectionTitleMatch:
				if sectionTitleMatch.group("sectionName") not in self.packageGroups and sectionTitleMatch.group("sectionName") != "Base OS Disk":
					raise Exception('Unknown section title: "%s" on line: %i of file: %s\n%s' % (sectionTitleMatch.group("sectionName"), lineNumber, fileLocation, line) ) # TODO: improve error handling
				
				currentSection = sectionTitleMatch.group("sectionName")
				continue
			
			# --------- item lines ---------
			packageLineMatch = self.packageLineParser.search(line)
			if packageLineMatch:
				if currentSection == None:
					# we have to have a place to put this
					raise Exception('Every item must belong to a section') # TODO: improve error handling
				
				thisPackage = installerPackage(
					displayName = packageLineMatch.group("displayName"),
					sourceLocation = packageLineMatch.group("fileLocation"),
					checksumString = packageLineMatch.group("fileChecksum"),
					mainCacheFolder = cacheFolderPath,
					additionalCacheFolders = userSuppliedPKGFolderPath
				)
				
				print('''	Checksum:	%(checksumType)s:%(checksum)s
	Source:		%(source)s
	Cache:		%(cacheLocation)s
''' % { "checksum":thisPackage.checksum, "checksumType":thisPackage.checksumType, "source":thisPackage.source, "cacheLocation":thisPackage.filePath })
				
				self.packageGroups[currentSection].append(thisPackage)
				
				continue
				
			# if we got here, the line was not good
			raise Exception('Error in config file: %s line number: %i\n%s' % (fileLocation, lineNumber, line)) # TODO: improve error handling
			
		inputfile.close()
		
	def arrangeFolders(self, sectionFolders=None):
		"Create the folder structure in the InstaDMG areas, and pop in soft-links to the items in the cache folder"
		
		assert isinstance(sectionFolders, list), "sectionfolders is required, and must be a list of dicts"
		
		import math
		
		for thisSectionFolder in sectionFolders:
			sectionTypes = thisSectionFolder["sections"]
			updateFolder = thisSectionFolder["folderPath"]
			
			itemsToProcess = []
			for thisSection in sectionTypes:
				itemsToProcess += self.packageGroups[thisSection]
			
			# Get the number of leading 0s we need
			leadingZeroes = 0
			if len(itemsToProcess) > 0:
				leadingZeroes = int(math.log10(len(itemsToProcess)))
			fileNameFormat = '%0' + str(leadingZeroes + 1) + "d %s"
			
			# Create symlinks for all of the items
			itemCounter = 1
			for thisItem in itemsToProcess:
				
				targetFileName = fileNameFormat % (itemCounter, thisItem.displayName)
				targetFilePath = os.path.realpath(os.path.join(updateFolder, targetFileName))
				
				if os.path.isabs(targetFilePath):
					os.symlink(thisItem.filePath, targetFilePath)
				else:
					pathFromTargetToSource = os.path.relpath(thisItem.filePath, os.path.dirname(targetFilePath))
					os.symlink(pathFromTargetToSource, targetFilePath)
				# ToDo: capture and better explain any errors here
				
				assert os.path.exists(targetFilePath), "Something went wrong linking from %s to %s" % (targetFilePath, pathFromTargetToSource) # this should catch bad links
				
				itemCounter += 1
				
		return True
	
	def setupInstaDMGFolders(self):
		'''Clean the chosen folders, and setup the package groups. This will only remove folders and symlinks, not actual data.'''
		
		assert isinstance(self.sectionFolders, list), "sectionfolders is required, and must be a list of dicts"
		
		for sectionFolder in self.sectionFolders:
			assert isinstance(sectionFolder, dict) and "folderPath" in sectionFolder and "sections" in sectionFolder, "sectionfolder information must be a dict with a 'folderPath' value, instead got: %s" % sectionFolder
			assert os.path.isdir(sectionFolder['folderPath']), "The sectionfolder did not seem to exist: %s" % sectionFolder['folderPath']
			assert os.path.isabs(sectionFolder['folderPath']), "setupInstaDMGFolders was passed a non-abs path to a folder: %s" % sectionFolder['folderPath']
			
			# clean the folders
			for thisItem in os.listdir(sectionFolder['folderPath']):
				
				# skip over a few types of items
				if thisItem in [".svn", ".DS_Store"]:
					continue
				
				pathToThisItem = os.path.join(sectionFolder['folderPath'], thisItem)
				
				# remove all links
				if os.path.islink(pathToThisItem):
					os.unlink(pathToThisItem)
					continue
				
				# remove the links from any folder, and if it then empty, remove it (otherwise bail)
				if os.path.isdir(pathToThisItem):
					for thisSubItem in os.listdir(pathToThisItem):
						pathToThisSubItem = os.path.join(pathToThisItem, thisSubItem)
						if os.path.islink(pathToThisSubItem):
							os.unlink(pathToThisSubItem)
						else:
							raise Exception('While cleaning folder: %s found a non-softlinked item: %s' % (sectionFolder['folderPath'], pathToThisSubItem))
					os.rmdir(pathToThisItem)
					continue
				
				raise Exception('While cleaning folder: %s found a non-softlinked item: %s' % (sectionFolder['folderPath'], pathToThisItem))
	
	def runInstaDMG(self, scratchFolder=None, outputFolder=None):
		
		assert isinstance(self.sectionFolders, list), "sectionFolders should be a list of hashes"
		# Todo: create a routine to validate things before th erun
		
		instaDMGCommand	= [ os.path.join(absPathToInstaDMGFolder, "instadmg.bash"), "-f" ]
		
		if self.catalogFileSettings.has_key("ISO Language Code"):
			instaDMGCommand += ["-i", self.catalogFileSettings["ISO Language Code"]]
			# TODO: check with installer to see if it will accept this language code
		
		if self.catalogFileSettings.has_key("Output Volume Name"):
			instaDMGCommand += ["-n", self.catalogFileSettings["Output Volume Name"]]
		
		if self.catalogFileSettings.has_key("Output File Name"):
			instaDMGCommand += ["-m", self.catalogFileSettings["Output File Name"]]
		
		if scratchFolder is not None:
			instaDMGCommand += ["-t", scratchFolder]
		
		for thisSectionFolder in self.sectionFolders:
			instaDMGCommand += ['-K', thisSectionFolder['folderPath']]
		
		if outputFolder is not None:
			instaDMGCommand += ["-o", outputFolder]

		print("Running InstaDMG: %s\n\n" % " ".join(instaDMGCommand))
		# we should be in the same directory as InstaDMG
		
		subprocess.call(instaDMGCommand)
		# TODO: a lot of improvements in handling of InstaDMG

class installerPackage:
	"This class represents a .pkg installer, and does much of the work."
		
	#---------------------Class Variables-----------------------------
	
	
	#--------------------Instance Variables---------------------------
	
	displayName			= None		# arbitrary text string for display	
	
	checksum			= None
	checksumType		= None
	
	source				= None
	filePath			= None		# a local location to link to
	
	#------------------------Functions--------------------------------
	
	def __init__(self, displayName, sourceLocation, checksumString, mainCacheFolder, additionalCacheFolders=None):	
		
		assert isinstance(displayName, str), "Recieved an empty or invalid name"
		assert sourceLocation is not None, "Recieved an empty location"
		assert isinstance(checksumString, str) is not None, "Recieved an empty or invalid checksum string"
		assert additionalCacheFolders is None or isinstance(additionalCacheFolders, str) or isinstance(additionalCacheFolders, list)
		
		assert checksumString.count(":") > 0, "Checksum string is not of the right format"
		checksumType, checksumValue = checksumString.split(":", 1)
		assert checksumType is not None, "There was no checksum type"
		assert checksumValue is not None, "There was no checksum"
		
		# confirm that hashlib supports the hash type:
		try:
			hashlib.new(checksumType)
		except ValueError:
			raise Exception("Hash type: %s is not supported by hashlib" % checksumType)
		
		# set basic values
		self.source = sourceLocation
		self.displayName = displayName
		self.checksum = checksumValue
		self.checksumType = checksumType
		
		# put together the list of cache folders
		cacheFolders = [mainCacheFolder]
		if isinstance(additionalCacheFolders, str):
			cacheFolders.append(additionalCacheFolders)
		elif isinstance(additionalCacheFolders, list):
			cacheFolders += additionalCacheFolders
		
		# values we need to find or create
		cacheFilePath = None
		
		print("Looking for %s" % displayName)
		
		# check the caches for an item with this checksum
		cacheFilePath = self.checkCacheForItem(None, checksumType, checksumValue, cacheFolders)
		if cacheFilePath is not None:
			print("	Found in cache folder by the checksum")
		
		else:
			# parse the location information
			parsedSourceLocationURL = urlparse.urlparse(sourceLocation)
			
			if parsedSourceLocationURL.scheme in [None, "file", ""]:
				
				assert parsedSourceLocationURL.params is "", "Unexpected url params in location: %s" % sourceLocation
				assert parsedSourceLocationURL.query is "", "Unexpected url query in location: %s" % sourceLocation
				assert parsedSourceLocationURL.fragment is "", "Unexpected url fragment in location: %s" % sourceLocation
				
				filePath = parsedSourceLocationURL.netloc + parsedSourceLocationURL.path
				
				# if this is a name (ie: not a path), look in the caches for the name
				if filePath.count("/") == 0:
					cacheFilePath = self.checkCacheForItem(filePath, checksumType, checksumValue, cacheFolders)
					
					if cacheFilePath is not None:
						print("	Found in cache folder by file name")
				
				# an absolute path
				if cacheFilePath is None and os.path.isabs(filePath) and os.path.exists(filePath):
					cacheFilePath = filePath
					
					print("	Found at the absolute path provided")
				
				# try this as a relative path from cwd
				if cacheFilePath is None and not os.path.isabs(filePath) and os.path.exists(filePath):
					cacheFilePath = os.path.abspath(filePath)

					print("	Found at the relative path provided")
				
				# try this path in each of the cache folders
				if cacheFilePath is None and not os.path.isabs(filePath):
					for cacheFolder in cacheFolders:
						if os.path.exists( os.path.join(cacheFolder, filePath) ):
							cacheFilePath = os.path.abspath(os.path.join(cacheFolder, filePath))
							print("	Found in the cache folder at the provided path")
							break
				
				# final check to make sure the file exists
				if cacheFilePath is None or not os.path.exists(cacheFilePath):
					raise FileNotFoundException("The referenced file/folder does not exist: %s" % cacheFilePath)
				
			elif parsedSourceLocationURL.scheme in ["http", "https"]:
				# url to download
				
				# guess the name from the URL
				cacheFilePath = self.checkCacheForItem(os.path.basename(parsedSourceLocationURL.path), checksumType, checksumValue, cacheFolders)
				if cacheFilePath is not None:
					print("	Found in cache folder by the name in the URL")
					
				else:
					# open a connection get a file name
					try:
						readFile = urllib2.urlopen(sourceLocation)
					except IOError, error:
						if hasattr(error, 'reason'):
							raise Exception('Unable to connect to remote url: %s got error: %s' % (sourceLocation, error.reason))
						elif hasattr(error, 'code'):
							raise Exception('Got status code: %s while trying to connect to remote url: %s' % (str(error.code), sourceLocation))
					
					if readFile is None:
						raise Exception("Unable to open file for checksumming: %s" % sourceLocation)
						
					# default the filename to the last bit of path of the url
					fileName = os.path.basename( urllib.unquote(urlparse.urlparse(readFile.geturl()).path) )
					expectedLength = None
					
					# grab the name of the file and its length from the http headers if avalible
					httpHeader = readFile.info()
					if httpHeader.has_key("content-length"):
						try:
							expectedLength = int(httpHeader.getheader("content-length"))
						except:
							pass # 
					
					if httpHeader.has_key("content-disposition"):
						fileName = httpHeader.getheader("content-disposition").strip()
					
					# check to see if we already have a file with this name and checksum
					cacheFilePath = self.checkCacheForItem(fileName, checksumType, checksumValue, cacheFolders)
					
					if cacheFilePath is not None:
						print("	Found using name in a redirected URL or content disposition header")
					
					if cacheFilePath is None:
						# continue downloading into the main cache folder
						hashGenerator = hashlib.new(checksumType)
						
						targetFilePath = os.path.join(mainCacheFolder, os.path.splitext(fileName)[0] + " " + checksumType + "-" + checksumValue + os.path.splitext(fileName)[1])
						
						checksum.cheksumFileObject(hashGenerator, readFile, fileName, expectedLength, copyToPath=targetFilePath, reportProgress=True, tabsToPrefix=1)
						
						if hashGenerator.hexdigest() != checksumValue:
							os.unlink(targetFilePath)
							raise Exception("Downloaded file did not match checksum: %s" % sourceLocation)
						
						cacheFilePath = targetFilePath
						print("	Downloaded and verified")
							
					readFile.close()
				
			else:
				raise Exception("Unknown or unsupported source location type: %s" % sourceLocation)
		
		# at this point we know that we have a file at cacheFilePath
		self.filePath = cacheFilePath
	
	@classmethod
	def checkCacheForItem(myClass, itemName, checksumType, checksumValue, cacheFolders):
		'''Look through the caches for this file'''
		
		assert checksumType is not None, "Checksum Type is required"
		assert checksumValue is not None, "Checksum is required"
		assert cacheFolders is not None, "Cache folders required"
		
		if isinstance(cacheFolders, str):
			cacheFolders = [cacheFolders]
		elif isinstance(cacheFolders, list):
			pass # in the right format
		else:
			raise Exception("cacheFolders is not the right format: %s" % cacheFolders)
		
		processReporter = None
		
		for thisCacheFolder in cacheFolders:
			assert os.path.isdir(thisCacheFolder), "The cache folder does not exist or is not a folder: %s" % thisCacheFolder
			
			# ToDo: think through the idea of having nested folders
			for thisItemName in os.listdir(thisCacheFolder):
				
				itemNameCheksum = os.path.splitext(thisItemName)[0].split(" ")[-1]
				
				# check for an item with the same name or if it contains the proper checksum value
				if itemName is not None and (thisItemName == itemName or itemNameCheksum == checksumType + "-" + checksumValue):
				
					if processReporter is None:
						processReporter = checksum.statusHandler(linePrefix="\t")
					
					if checksumValue == checksum.checksum(os.path.join(thisCacheFolder, thisItemName), checksumType=checksumType, progressReporter=processReporter)['checksum']:
						return os.path.join(thisCacheFolder, thisItemName)
		
		return None
	
#--------------------------------MAIN--------------------------------

def print_version(option, opt, value, optionsParser):
	optionsParser.print_version()
	sys.exit(0)

def cleanupTempFolder(tempFolder):
	if os.path.exists(tempFolder) and os.path.isdir(tempFolder):
		# ToDo: log this
		shutil.rmtree(tempFolder, ignore_errors=True)

def main ():
	
	# ------- defaults -------
	
	outputVolumeName	= "MacintoshHD"
	outputFileName		= str(datetime.date.today().month) + "-" + str(datetime.date.today().day) + "-" + str(datetime.date.today().year)
	
	# ---- parse options ----
	
	import optparse
	optionsParser = optparse.OptionParser("%prog [options] catalogFile1 [catalogFile2 ...]", version="%%prog %s" % versionString)
	optionsParser.add_option("-a", "--add-catalog", action="append", type="string", dest="addOnCatalogFiles", help="Add the items in this catalog file to all catalog files processed. Can be called multiple times", metavar="FILE_PATH")
	optionsParser.add_option("-v", "", action="callback", callback=print_version, help="Print the version number and quit")
	
	# instaDMG options
	
	optionsParser.add_option("-p", "--process", action="store_true", default=False, dest="processWithInstaDMG", help="Run InstaDMG for each catalog file processed")
	optionsParser.add_option("", "--instadmg-scratch-folder", action="store", dest="instadmgScratchFolder", default=None, type="string", metavar="FOLDER_PATH", help="Tell InstaDMG to use FOLDER_PATH as the scratch folder")
	optionsParser.add_option("", "--instadmg-output-folder", action="store", dest="instadmgOutputFolder", default=None, type="string", metavar="FOLDER_PATH", help="Tell InstaDMG to place the output image in FOLDER_PATH")
	
	# sourceFolder options
	
	optionsParser.add_option('', '--add-catalog-folder', action='append', default=None, type='string', dest='catalogFolders', help='Override the folders that are searched for catalog files', metavar="FILE_PATH")
	
	
	options, catalogFiles = optionsParser.parse_args()
	
	# --- process options ---
	
	if options.catalogFolders is None:
		options.catalogFolders = os.path.normpath(os.path.join(absPathToInstaDMGFolder, "AddOns", "InstaUp2Date", "CatalogFiles"))
	
	# --- police options ----
	
	if len(catalogFiles) < 1:
		optionsParser.error("At least one catalog file is required")
	
	if (options.instadmgScratchFolder != None or options.instadmgOutputFolder != None) and options.processWithInstaDMG == False:
		optionsParser.error("The instadmg-scratch-folder and instadmg-output-folder options require the -p/--process option to also be enabled")
	
	if options.instadmgScratchFolder != None and not os.path.isdir(options.instadmgScratchFolder):
		optionsParser.error("The instadmg-scratch-folder option requires a valid folder path, but got: %s" % options.instadmgScratchFolder)
	
	if options.instadmgOutputFolder != None and not os.path.isdir(options.instadmgOutputFolder):
		optionsParser.error("The instadmg-output-folder option requires a valid folder path, but got: %s" % options.instadmgOutputFolder)
	
	# if we are running InstaDMG, then we need to be running as root
	if options.processWithInstaDMG is True and os.getuid() != 0:
		optionsParser.error("When using the -p/--process flag this must be run as root (sudo is fine)")
	
	# --- process options ---
	
	baseCatalogFiles = []
	for thisCatalogFile in catalogFiles:
		try:
			baseCatalogFiles.append(instaUpToDate.getCatalogFullPath(thisCatalogFile, options.catalogFolders))
			
		except CatalogNotFoundException:
			optionsParser.error("There does not seem to be a catalog file at: %s" % thisCatalogFile)
	
	addOnCatalogFiles = []
	if options.addOnCatalogFiles is not None:
		for thisCatalogFile in options.addOnCatalogFiles:
			try:
				addOnCatalogFiles.append(instaUpToDate.getCatalogFullPath(thisCatalogFile, self.catalogFolders))
			
			except CatalogNotFoundException:
				optionsParser.error("There does not seem to be a catalog file at: %s" % thisCatalogFile)
	
	sectionFolders = None
	if options.processWithInstaDMG == True:
		tempFolder = tempfile.mkdtemp(prefix='InstaUp2DateFolder-', dir="/tmp")
		
		atexit.register(cleanupTempFolder, tempFolder)
		
		sectionFolders = [
			{"folderPath":tempFolder, "sections":["OS Updates", "System Settings", "Apple Updates", "Third Party Software", "Third Party Settings", "Software Settings"]}
		]
	else:
		sectionFolders = [
			{"folderPath":os.path.join(absPathToInstaDMGFolder, "InstallerFiles/BaseUpdates"), "sections":["OS Updates", "System Settings"]},
			{"folderPath":os.path.join(absPathToInstaDMGFolder, "InstallerFiles/CustomPKG"), "sections":["Apple Updates", "Third Party Software", "Third Party Settings", "Software Settings"]}
		]
	# ----- run process -----
	
	for catalogFilePath in baseCatalogFiles:
		
		thisController = instaUpToDate(sectionFolders, options.catalogFolders)
		
		# setup for the run
		thisController.setupInstaDMGFolders()
		
		# parse the tree of catalog files
		thisController.parseFile(catalogFilePath)
		
		# add any additional catalogs to this one
		for addOnCatalogFile in addOnCatalogFiles:
			thisController.parseFile(addOnCatalogFile)
		
		# create the folder strucutres needed
		thisController.arrangeFolders(sectionFolders=sectionFolders)
		
		if options.processWithInstaDMG == True:
			# the run succeded, and it has been requested to run InstaDMG
			thisController.runInstaDMG(scratchFolder=options.instadmgScratchFolder, outputFolder=options.instadmgOutputFolder)
		
		
#------------------------------END MAIN------------------------------

if __name__ == "__main__":
    main()
