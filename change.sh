# 1. Create a mount point (if it doesn't exist)
mkdir -p /tmp/my-rootfs

# 2. Mount the rootfs file
# The loop device allows us to mount a file as if it were a physical disk
sudo mount rootfs.ext4 /tmp/my-rootfs

# 3. Copy your local agent.py into the mounted filesystem
# Based on your Dockerfile, it lives at /bin/agent.py
sudo cp agent.py /tmp/my-rootfs/bin/agent.py

# 4. Unmount the filesystem (Crucial! effectively "saves" the changes)
sudo umount /tmp/my-rootfs

echo "Updated agent.py in rootfs.ext4"