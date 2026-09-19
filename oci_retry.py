"""
OCI ARM Retry Script
Tự động retry tạo instance VM.Standard.A1.Flex (2 OCPU / 12 GB RAM)
cho đến khi thành công hoặc phát hiện instance đã tồn tại.

Author: taiphantan28
"""

import oci
import os
import time
import sys
import datetime

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

# Tenancy OCID (đọc từ env var)
COMPARTMENT_ID = os.environ.get("OCI_TENANCY")

# Subnet OCID (đọc từ env var - BẮT BUỘC)
SUBNET_ID = os.environ.get("OCI_SUBNET_ID")

# SSH public key của bạn
SSH_PUBLIC_KEY = """ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCZ9ejGngFmxxUWMbDhGsJV39jPl8MmIoAZ8OWU9oNXdnRL+RrRAp49z2RrKUJEwCnl3evEeuNlFL8oFGq3PCJwsgeOscAvGc+Qo1tWnEarwDtO9YrvdyUvFw8DC/7FB8v62nY/WlOVyFsebeEy+v3LLG3BOuYrGpxtBkS4xmbQV5h7MHDyO08iEVwhnlF0M5wM2cC9UVNVOh30KzjpPECYMOO9KEzd1VYY7/1qc1gDO5dtUXmTJ7NjJSQQY0smQgV49Wu+AMTJx6msjEMYOTtqpCUzJyRUmZ3sQrAWuUjULbvTjU9knEXaHIvL3HkBH5OVboWwLNgFIQKUOkDhjbOL ssh-key-2026-09-19"""

# Tên instance
INSTANCE_NAME = "n8n-server"

# Cấu hình shape
SHAPE = "VM.Standard.A1.Flex"
OCPUS = 2
MEMORY_GB = 12
BOOT_VOLUME_GB = 100

# Retry interval (giây)
RETRY_INTERVAL = 60

# ══════════════════════════════════════════════════════════════
# HÀM HỖ TRỢ
# ══════════════════════════════════════════════════════════════

def get_availability_domain(identity_client, compartment_id):
    """Lấy Availability Domain đầu tiên."""
    ads = identity_client.list_availability_domains(compartment_id).data
    return ads[0].name


def get_ubuntu_image(compute_client, compartment_id):
    """Tìm image Ubuntu mới nhất tương thích ARM."""
    images = compute_client.list_images(
        compartment_id,
        operating_system="Canonical Ubuntu",
        shape=SHAPE,
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data
    
    for img in images:
        if "22.04" in img.display_name:
            return img.id
    
    if images:
        return images[0].id
    
    raise Exception("Không tìm thấy Ubuntu image")


def check_instance_exists(compute_client, compartment_id):
    """Kiểm tra instance đã tồn tại chưa."""
    instances = compute_client.list_instances(
        compartment_id,
        display_name=INSTANCE_NAME,
    ).data
    
    for inst in instances:
        if inst.lifecycle_state not in ["TERMINATED", "TERMINATING"]:
            return True
    return False


def try_create_instance(compute_client, compartment_id, ad, image_id):
    """Thử tạo 1 instance. Trả về True nếu thành công."""
    try:
        details = oci.core.models.LaunchInstanceDetails(
            compartment_id=compartment_id,
            availability_domain=ad,
            display_name=INSTANCE_NAME,
            shape=SHAPE,
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=OCPUS,
                memory_in_gbs=MEMORY_GB,
            ),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                source_type="image",
                image_id=image_id,
                boot_volume_size_in_gbs=BOOT_VOLUME_GB,
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                assign_public_ip=True,
                subnet_id=SUBNET_ID,
            ),
            metadata={
                "ssh_authorized_keys": SSH_PUBLIC_KEY,
            },
        )
        
        response = compute_client.launch_instance(details)
        print(f"✅ TẠO THÀNH CÔNG! Instance OCID: {response.data.id}")
        return True
    
    except oci.exceptions.ServiceError as e:
        if "Out of host capacity" in str(e.message) or e.status == 500:
            print(f"⏳ [{datetime.datetime.now().strftime('%H:%M:%S')}] Out of capacity. Retry sau {RETRY_INTERVAL}s...")
        else:
            print(f"❌ Lỗi: {e.status} - {e.message}")
        return False
    except Exception as e:
        print(f"❌ Lỗi không xác định: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("OCI ARM Retry Script — Bắt đầu")
    print(f"Instance name: {INSTANCE_NAME}")
    print(f"Shape: {SHAPE} ({OCPUS} OCPU / {MEMORY_GB} GB RAM)")
    print("=" * 60)
    
    if not COMPARTMENT_ID:
        print("❌ Thiếu OCI_TENANCY trong environment.")
        sys.exit(1)
    
    if not SUBNET_ID:
        print("❌ Thiếu OCI_SUBNET_ID trong environment.")
        sys.exit(1)
    
    try:
        config = oci.config.from_file()
    except Exception as e:
        print(f"❌ Không đọc được OCI config: {e}")
        sys.exit(1)
    
    identity_client = oci.identity.IdentityClient(config)
    compute_client = oci.core.ComputeClient(config)
    
    if check_instance_exists(compute_client, COMPARTMENT_ID):
        print(f"✅ Instance '{INSTANCE_NAME}' đã tồn tại. Không cần tạo thêm.")
        sys.exit(0)
    
    ad = get_availability_domain(identity_client, COMPARTMENT_ID)
    print(f"📍 Availability Domain: {ad}")
    
    image_id = get_ubuntu_image(compute_client, COMPARTMENT_ID)
    print(f"🖼️  Image: {image_id}")
    print()
    
    max_attempts = 5
    for i in range(max_attempts):
        print(f"🔄 Lần thử {i+1}/{max_attempts}...")
        success = try_create_instance(compute_client, COMPARTMENT_ID, ad, image_id)
        if success:
            sys.exit(0)
        
        if i < max_attempts - 1:
            time.sleep(RETRY_INTERVAL)
    
    print("⏳ Hết lượt thử trong workflow này. GitHub Actions sẽ tự chạy lại sau 5 phút.")
    sys.exit(0)


if __name__ == "__main__":
    main()