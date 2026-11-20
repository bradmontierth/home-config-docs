import subprocess,sys

def query(sql):
 p=subprocess.run(['docker','exec','-i','mariadb-mariadb-1','sh','-c','MYSQL_PWD="${MARIADB_ROOT_PASSWORD:-$MYSQL_ROOT_PASSWORD}" exec mariadb -uroot --batch --raw hubitat_logging'],input=sql,text=True,capture_output=True)
 if p.returncode:raise RuntimeError(p.stderr)
 return p.stdout
if __name__=='__main__':print(query(sys.stdin.read()))
