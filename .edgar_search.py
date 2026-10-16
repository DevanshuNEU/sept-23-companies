import sys,re,subprocess,urllib.parse,time,html as H
UA="research chicholikar.d@northeastern.edu"
def curl(url):
    for i in range(4):
        r=subprocess.run(["curl","-s","--max-time","30","-A",UA,url],capture_output=True,text=True).stdout
        if r and 'Request Rate Threshold' not in r and 'File Unavailable' not in r and len(r)>500:
            return r
        time.sleep(2+2*i)
    return r
def search(name):
    url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=%s&type=&dateb=&owner=include&count=100"%urllib.parse.quote(name)
    h=curl(url)
    rows=re.findall(r'CIK=(\d{10})&amp;[^"]*">\d+</a></td>\s*<td scope="row">(.*?)</td>\s*<td[^>]*><a[^>]*>(.*?)</a>',h,re.S)
    out=[(c,H.unescape(re.sub('<[^>]+>','',n)).strip(),s.strip()) for c,n,s in rows]
    if not out:
        m=re.search(r'<span class="companyName">(.*?)</span>',h,re.S)
        if m:
            t=H.unescape(re.sub('<[^>]+>','',m.group(1))).strip()
            c=re.search(r'CIK#:\s*(\d{10})',t)
            out.append(((c.group(1) if c else '?'), t, 'single'))
        elif 'No matching' in h or 'companyMatch' not in h:
            out=[]
    return out
for n in sys.argv[1:]:
    print("###",n)
    r=search(n)
    if not r: print("   NO RESULTS")
    for x in r[:60]: print("   ",x[0],"|",x[1],"|",x[2])
    time.sleep(1.2)
