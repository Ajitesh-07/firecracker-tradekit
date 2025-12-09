sudo docker build -t tradekit-guest .

# 2. Create container and export
id=$(sudo docker create tradekit-guest)
sudo docker export $id > rootfs.tar
sudo docker rm -v $id

# 3. Create a 1GB empty disk (Count=3000)
dd if=/dev/zero of=rootfs.ext4 bs=1M count=1024

# 4. Format as Ext4
mkfs.ext4 rootfs.ext4

# 5. Copy files (This will take a moment)
mkdir -p /tmp/my-rootfs
sudo mount rootfs.ext4 /tmp/my-rootfs
sudo tar -xvf rootfs.tar -C /tmp/my-rootfs
sudo umount /tmp/my-rootfs

# Cleanup
rm rootfs.tar