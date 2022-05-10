#/bin/sh
FILE="$HOME/.ssh/id_rsaaefa"
if [ -f $FILE ];then
    echo "$FILE exists"
else
    echo "$FILE doesn't exist"
fi