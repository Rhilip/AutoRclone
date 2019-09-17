from google.oauth2.service_account import Credentials
from googleapiclient.errors import HttpError
import googleapiclient.discovery, progress.bar, time, threading, httplib2shim, glob, sys, argparse, socket, json

# GLOBAL VARIABLES
account_count = 0
dtu = 1
drive = []
retryable_requests = []
unretryable_requests = []
threads = None

# DOCUMENTED ERROR CODES & REASONS
error_code_reasons = {
    "retryable": {
        403: ['dailyLimitExceeded', 'userRateLimitExceeded', 'rateLimitExceeded', 'sharingRateLimitExceeded', 'appNotAuthorizedToFile', 'insufficientFilePermissions', 'domainPolicy'],
        429: ['rateLimitExceeded'],
        500: ['backendError','internalError']
    },
    "unretryable": {
        400: ['badRequest', 'invalidSharingRequest'],
        401: ['authError'],
        404: ['notFound'],
    }
}

httplib2shim.patch()

class DriveQuotadError(Exception):
    pass

class CopyService:
    def increase_request_dtu_and_retry(self):
        global drive
        global account_count
        
        if self.dtu + 1 == account_count:
            self.dtu = 1
        else:
            self.dtu += 1
        self.request = drive[self.dtu].files().copy(
            fileId=self.fileId,
            body=self.body,
            supportsAllDrives=True
        )

        try:
            self.response = apicall(self.request)
        except DriveQuotadError:
            self.increase_request_dtu_and_retry()

    def __init__(self, fileId, body):
        global dtu
        self.dtu = dtu
        self.fileId = fileId
        self.body = body
        self.request = drive[self.dtu].files().copy(fileId=fileId,body=body,supportsAllDrives=True)
        
        try:
            self.response = apicall(self.request)
        except DriveQuotadError:
            self.increase_request_dtu_and_retry()

def apicall(request):
    # MODIFY THE VAR BELOW INCASE YOU WANT TO MODIFY THE SLEEP TIME BETWEEN EACH RETRY ATTEMPT
    sleep_time = 3
    resp = None

    while True:
        try:
            resp = request.execute()
        except HttpError as error:
            error_details = json.loads(error.content.decode("utf-8"))
            code = error_details["error"]["code"]
            reason = error_details["error"]["errors"][0]["reason"]
            if code == 403 and reason == 'userRateLimitExceeded':
                raise DriveQuotadError
            elif is_retryable_error(code, reason, request):
                time.sleep(sleep_time)
                continue
            else:
                return None
        except socket.error:
            time.sleep(sleep_time)
            continue
        else:
            return resp
        break

# FUNCTION TO CHECK IF ERROR RETURNED IS RETRYABLE OR NOT
def is_retryable_error(code, reason, request):
    global error_code_reasons

    if code in error_code_reasons["retryable"]:
        if reason not in error_code_reasons["retryable"][code]:
            # UNDOCUMENTED REASON, BUT RETRYABLE
            print("Retryable error, with undocumented reason.")
            print("Error Code: " + str(code) + ", Error Reason: " + reason)
        return True
    elif code in error_code_reasons["unretryable"]:
        if reason not in error_code_reasons["unretryable"][code]:
            # UNDOCUMENTED REASON WITH UNRETRYABLE CODE
            print("Unretryable error, with undocumented reason.")
            print("Error Code: " + str(code) + ", Error Reason: " + reason)
        return False
    else:
        # UNKNOWN CODE ERROR
        print("Undocumented API Response code.")
        print("Error Code: " + str(code) + ", Error Reason: " + reason)
        return False

def ls(parent, searchTerms=""):
    files = []
    
    resp = apicall(
	    drive[0].files().list(
		    q="'%s' in parents" % parent + searchTerms,
		    pageSize=1000,
		    supportsAllDrives=True,
		    includeItemsFromAllDrives=True
	    )
	)
    files += resp["files"]

    while "nextPageToken" in resp:
        resp = apicall(
            drive[0].files().list(
                q="'%s' in parents" % parent + searchTerms,
                pageSize=1000,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageToken=resp["nextPageToken"]
            )
        )
        files += resp["files"]
    return files

def lsd(parent):
    return ls(
        parent,
        searchTerms=" and mimeType contains 'application/vnd.google-apps.folder'"
    )

def lsf(parent):
    return ls(
        parent,
        searchTerms=" and not mimeType contains 'application/vnd.google-apps.folder'"
    )

def copy(source, dest):
    global threads
    global unretryable_requests

    copy_service = CopyService(
        fileId=source,
        body={
            "parents": [dest]
        }
    )

    if copy_service.response == None:   
        unretryable_requests.append(source)
        if len(unretryable_requests) > 0:
            print("unretryable request")
            sys.exit()
    else:
        pass

    threads.release()

def rcopy(source, dest, sname, pre, width):
    global drive
    global threads
    global retryable_requests

    local_retryable_requests = []
    pres = pre

    filestocopy = lsf(source)
    num_files = len(filestocopy)

    if num_files > 0:
        for file in filestocopy:
            local_retryable_requests.append(file["id"])
        
        pbar = progress.bar.Bar(pres + sname, max=num_files)
        pbar.update()
        while len(local_retryable_requests) > 0:
            for fileId in local_retryable_requests:
                copyfileId = fileId
                local_retryable_requests.remove(fileId)
                
                threads.acquire()
                thread = threading.Thread(
                    target=copy,
                    args=(
                        copyfileId,
                        dest
                    )
                )
                thread.start()
            copied_files = num_files - len(retryable_requests)
            for file in range(copied_files):
                tempfile = file
                retryable_requests.remove(tempfile)
                local_retryable_requests.append(tempfile)
                pbar.next()
        pbar.finish()

    else:
        print(pres + sname)
    
    folderstocopy = lsd(source)
    fs = len(folderstocopy) - 1
    s = 0
    for folder in folderstocopy:
        if s == fs:
            nstu = pre.replace("├" + "─" * width + " ", "│" + " " * width + " ").replace("└" + "─" * width + " ", "  " + " " * width) + "└" + "─" * width + " "
        else:
            nstu = pre.replace("├" + "─" * width + " ", "│" + " " * width + " ").replace("└" + "─" * width + " ", "  " + " " * width) + "├" + "─" * width + " "
        resp = drive[0].files().create(
            body={
                "name": folder["name"],
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [dest]
            },
            supportsAllDrives=True
        ).execute()
        
        rcopy(
            folder["id"],
            resp["id"],
            folder["name"].replace('%', "%%"),
            nstu,
            width
        )
        s += 1

def multifolderclone(
    source=None,
    dest=None,
    path='accounts',
    width=2
):
    global account_count
    global drive
    global threads

    stt = time.time()
    accounts = glob.glob(path + '/*.json')

    if source == None:
        source = input("Source Folder ID Missing. Please enter Folder ID of source: ")
    else:
        if dest == None:
            dest = input("Destination Folder ID Missing. Please enter Folder ID of destination: ")
        else:
            while len(accounts) == 0:
                path = input("No service accounts found in current directory. Please enter the path where the accounts are located at: ")
                accounts = glob.glob(path + '/*.json')

    print('Copy from ' + source + ' to ' + dest + '.')
    print('View set to tree (' + str(width) + ').')
    pbar = progress.bar.Bar("Creating Drive Services", max=len(accounts))

    for account in accounts:
        account_count += 1
        credentials = Credentials.from_service_account_file(account, scopes=[
            "https://www.googleapis.com/auth/drive"
        ])
        drive.append(googleapiclient.discovery.build("drive", "v3", credentials=credentials))
        pbar.next()
    pbar.finish()

    threads = threading.BoundedSemaphore(account_count)
    print('BoundedSemaphore with %d threads' % account_count)

    try:
        rcopy(source, dest, "root", "", width)
    except KeyboardInterrupt:
        print('Quitting')
        sys.exit()

    print('Complete.')
    hours, rem = divmod((time.time() - stt), 3600)
    minutes, sec = divmod(rem, 60)
    print("Elapsed Time:\n{:0>2}:{:0>2}:{:05.2f}".format(int(hours), int(minutes), sec))

def main():
    parse = argparse.ArgumentParser(description='A tool intended to copy large files from one folder to another.')
    parse.add_argument('--width', '-w', default=2, help='Set the width of the view option.')
    parse.add_argument('--path', '-p', default='sa1', help='Specify an alternative path to the service accounts.')
    parsereq = parse.add_argument_group('required arguments')
    parsereq.add_argument('--source-id', '-s',default='1tLO819B_VpYTg1muTUBcyFuO3Tee9FuG',help='The source ID of the folder to copy.')
    parsereq.add_argument('--destination-id', '-d',default='1KJsiNzt8prYH6-sQdhwaA6cf4IbT-Woe',help='The destination ID of the folder to copy to.')
    args = parse.parse_args()

    multifolderclone(
        args.source_id,
        args.destination_id,
        args.path,
        args.width
    )

if __name__ == '__main__':
    main()