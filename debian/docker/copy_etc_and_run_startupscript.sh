#!/bin/bash

DEFAULT_USER="admin"
DEFAULT_PASSWORD="admin"
DOCKER_ETC_PATH=/config/etc
REMOTE_TMP_PATH=/tmp/
# location of startup script in the docker container
STARTUPSCRIPT=/config/startup.sh

handle_args() {
    # Parse options
    while getopts 'u:p:' OPTION; do
        case "$OPTION" in
            u)
            user="$OPTARG"
            ;;
            p)
            password="$OPTARG"
            ;;
            ?)
            usage
            exit 1
            ;;
        esac
    done
    shift "$(($OPTIND -1))"

    # Assign defaults if options weren't provided
    if [ -z "$user" ] ; then
        user=$DEFAULT_USER
    fi
    if [ -z "$password" ] ; then
        password=$DEFAULT_PASSWORD
    fi
    
    SSH_CMD="sshpass -p $password ssh -o LogLevel=ERROR -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p22"
    SCP_CMD="sshpass -p $password scp -o LogLevel=ERROR -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -P22"
    HOST="$user@localhost"

    # Parse commands
    case $1 in

    startupconfig)
        startupconfig 
        ;;

    copyetc)
        copyetc 
        ;;

    *)
        usage
        ;;
    esac
}

usage() {
	echo "Usage: $(basename $0) [-u USERNAME] [-p PASSWORD] COMMAND"
    echo "Options:"
    echo "  -u USERNAME    VM SSH username (default: admin)"
    echo "  -p PASSWORD    VM SSH password (default: admin)"
    echo
    echo "Commands:"
    echo "  startupconfig         copy $STARTUPSCRIPT to vm and run it"
    echo "  copyetc               copy $DOCKER_ETC_PATH to /etc on the vm"
	exit 0;
}

startupconfig() {
    if [ -f "$STARTUPSCRIPT" ]; then
        echo "copy_etc_and_run_startupscript.sh startupconfig(): Copying $STARTUPSCRIPT to vm..."
	# Put startupfile file to VM under ~/ (/home/clab for the debian image).
        $SCP_CMD $STARTUPSCRIPT $HOST:startup.sh && $SSH_CMD $HOST "sudo chmod +x startup.sh && sudo ./startup.sh || true"
    else 
	    echo "copy_etc_and_run_startupscript.sh startupconfig(): $STARTUPSCRIPT not found. No startupscript to upload."
    fi
}

copyetc() {
    if [ -d "$DOCKER_ETC_PATH" ]; then
	echo "copy_etc_and_run_startupscript.sh copyetc(): Copying $DOCKER_ETC_PATH to vm..."
        # Copy /config/etc to /etc in the VM.
#        $SCP_CMD -r $DOCKER_ETC_PATH $HOST:$REMOTE_TMP_PATH && $SSH_CMD $HOST "sudo cp -r $REMOTE_TMP_PATH/* /etc" && $SSH_CMD $HOST "sudo shutdown -r now || true"
	$SCP_CMD -r $DOCKER_ETC_PATH $HOST:$REMOTE_TMP_PATH && $SSH_CMD $HOST "sudo cp -r $REMOTE_TMP_PATH/* /etc"
    else 
	    echo "copy_etc_and_run_startupscript.sh copyetc(): $DOCKER_ETC_PATH not found. Nothing to upload."
    fi
}

handle_args "$@"
