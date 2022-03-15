import os
line_all = []
str_key = []
str_value = []

with open('/home/wk/sd_hibench/spark/spark-bodropout-test/hibench/report/hibench.report') as f:
    for line in f:
#        print(type(line), '\n')
        line_all.append(line)
str_key = line_all[0].split()
str_value = line_all[1].split()
print(str_key, '\n')
print(str_value, '\n')


with open('/home/wk/sd_hibench/spark/spark-bodropout-test/hibench/report/hibench', 'a+') as f2:
    for i in range(len(str_key)):
        f2.writelines(str_key[i]+', '+str_value[i]+os.linesep)
