#!/usr/bin/env bash

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36"
MAX_JOBS=10
BASE_FILE="./playlists/vivatvsports.m3u8"
README="./README.md"

[[ ! -f $BASE_FILE ]] && {
    echo "$BASE_FILE does not exist" >&2
    exit 1
}

shopt -s nocasematch

STATUSLOG=$(mktemp)

get_status() {
    local url="$1"
    local channel="$2"
    local index="$3"
    local total="$4"
    local referer="$5"

    local chnl_info response rc IFS status_code content_type index_width

    [[ $url != http* ]] && return

    printf -v chnl_info "%s (%s)\n" "$channel" "$url"

    response=$(
        curl -skL \
            -A "$UA" \
            -H "Accept: */*" \
            -H "Accept-Language: en-US,en;q=0.9" \
            -H "Connection: keep-alive" \
            -o /dev/null \
            -e "$referer" \
            --compressed \
            --max-time 10 \
            -w "%{http_code}|%{content_type}" \
            "$url" 2>&1
    )

    rc=$?

    IFS="|" read -r status_code content_type <<<"$response"

    index_width=${#total}

    if ((rc != 0)); then
        if [[ $status_code == 2* && $rc == 28 ]]; then
            printf "[%${index_width}d/%d]\t%b\t%s" \
                "$index" "$total" "\u2714\ufe0f" "$chnl_info"

        else
            printf "[%${index_width}d/%d]\t%b\t%s" \
                "$index" "$total" "\U274C" "$chnl_info"

            printf "%s\t%s\tcURL Error (%s)\n" \
                "$url" "$channel" "$rc" >>"$STATUSLOG"
        fi

    elif [[ $status_code != 2* ]]; then
        printf "[%${index_width}d/%d]\t%b\t%s" \
            "$index" "$total" "\U274C" "$chnl_info"

        printf "%s\t%s\tHTTP Error (%s)\n" \
            "$url" "$channel" "$status_code" >>"$STATUSLOG"

    else
        case "$content_type" in

        application/vnd.apple.mpegurl* | \
            application/x-mpegURL* | \
            application/octet-stream* | \
            video/mpeg* | \
            video/mp2t* | \
            audio/x-mpegurl | \
            text/plain*)

            printf "[%${index_width}d/%d]\t%b\t%s" \
                "$index" "$total" "\u2714\ufe0f" "$chnl_info"
            ;;

        text/html* | *)

            printf "[%${index_width}d/%d]\t%b\t%s" \
                "$index" "$total" "\U274C" "$chnl_info"

            printf "%s\t%s\tInvalid Source (%s)\n" \
                "$url" "$channel" "$status_code" >>"$STATUSLOG"
            ;;
        esac
    fi
}

check_links() {
    local total_urls=$1

    local channel_num=1
    local name=""

    local IFS line referer

    printf "Checking %d links from %s\n\n" "$total_urls" "$BASE_FILE"

    while IFS= read -r line; do
        line=${line//$'\r'/}

        if [[ $line == \#EXTINF* ]]; then
            name=$(sed -n 's/.*tvg-name="\([^"]*\)".*/\1/p' <<<"$line")

            [[ -z $name ]] && name="Channel $channel_num"

            referer="https://google.com"

        elif [[ $line == \#EXTVLCOPT:http-referrer=* ]]; then
            referer=${line#*=}

        elif [[ $line =~ ^https?:// ]]; then
            while (($(jobs -rp | wc -l) >= MAX_JOBS)); do wait -n; done

            get_status "$line" "$name" "$channel_num" "$total_urls" "$referer" &

            ((channel_num++))
        fi

    done <"$BASE_FILE"

    wait
    echo -e "\nDone."
}

write_readme() {
    local total=$1
    local failed_count=0
    local online_count=0

    [[ -f "$STATUSLOG" ]] && failed_count=$(wc -l <"$STATUSLOG")
    online_count=$((total - failed_count))

    cat <<EOF >"$README"
- **Total Checked:** $total
- **Online:** $online_count
- **Offline / Failed:** $failed_count
- **Last Checked:** $(date -u +'%Y-%m-%d %H:%M:%S UTC')

| Channel | URL | Issue |
| :--- | :--- | :--- |
EOF

    if ((failed_count > 0)); then
        while IFS=$'\t' read -r url channel error; do
            printf "| %s | \`%s\` | %s |\n" "$channel" "$url" "$error" >>"$README"
        done <"$STATUSLOG"
    else
        echo -e "\nAll streams are currently online! 🎉" >>"$README"
    fi
}

total_urls=$(grep -cE '^https?://' "$BASE_FILE")

check_links "$total_urls"
write_readme "$total_urls"
rm "$STATUSLOG"
