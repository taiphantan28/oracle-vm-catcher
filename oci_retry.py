"""
OCI ARM Retry Script v4 FINAL
- Shape: 1 OCPU / 6 GB (khớp hạn mức account)
- Multi-AD
- Ưu tiên Ubuntu 26.04 (fallback 24.04, 22.04)
- Xử lý 429 đúng cách (exit loop, không sleep)
- Tìm image linh hoạt

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

COMPARTMENT_ID = os.environ.get("OCI_TENANCY")
SUBNET_ID = os.environ.get("OCI_SUBNET_ID")

# ⚠️ BẮT BUỘC THAY: Public key CỦA BẠN
SSH_PUBLIC_KEY = """ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCZ9ejGngFmxxUWMbDhGsJV39jPl8MmIoAZ8OWU9oNXdnRL+RrRAp49z2RrKUJEwCnl3evEeuNlFL8oFGq3PCJwsgeOscAvGc+Qo1tWnEarwDtO9YrvdyUvFw8DC/7FB8v62nY/WlOVyFsebeEy+v3LLG3BOuYrGpxtBkS4xmbQV5h7MHDyO08iEVwhnlF0M5wM2cC9UVNVOh30KzjpPECYMOO9KEzd1VYY7/1qc1gDO5dtUXmTJ7NjJSQQY0smQgV49Wu+AMTJx6msjEMYOTtqpCUzJyRUmZ3sQrAWuUjULbvTjU9knEXaHIvL3HkBH5OVboWwLNgFIQKUOkDhjbOL ssh-key-2026-09-19"""

INSTANCE_NAME = "n8n-server"
SHAPE = "VM.Standard.A1.Flex"

# ⭐ 1 OCPU / 6 GB — khớp hạn mức Free Trial
OCPUS = 1
MEMORY_GB = 6
BOOT_VOLUME_GB = 100  # Đủ cho n8n + Docker; tăng lên 100 nếu muốn

RETRY_INTERVAL = 120
MAX_ATTEMPTS_PER_AD = 1


# ══════════════════════════════════════════════════════════════
# HÀM HỖ TRỢ
# ══════════════════════════════════════════════════════════════

def get_all_availability_domains(identity_client, compartment_id):
    ads = identity_client.list_availability_domains(compartment_id).data
    return [ad.name for ad in ads]


def get_ubuntu_image(compute_client, compartment_id):
    """
    Tìm image Ubuntu mới nhất tương thích ARM.
    Ưu tiên: 26.04 (non-Minimal) → 26.04 (Minimal) → 24.04 → 22.04
    """
    images = compute_client.list_images(
        compartment_id,
        operating_system="Canonical Ubuntu",
        shape=SHAPE,
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data
    
    print(f"🔍 Có {len(images)} images Ubuntu cho shape {SHAPE}")
    
    # Ưu tiên 26.04 NON-Minimal
    for version in ["26.04", "24.04", "22.04"]:
        for img in images:
            if version in img.display_name and "Minimal" not in img.display_name:
                print(f"✅ Chọn: {img.display_name}")
                return img.id
    
    # Fallback: bất kỳ version nào (kể cả Minimal)
    for version in ["26.04", "24.04", "22.04"]:
        for img in images:
            if version in img.display_name:
                print(f"⚠️ Fallback Minimal: {img.display_name}")
                return img.id
    
    # Fallback cuối: image đầu tiên
    if images:
        print(f"⚠️ Fallback image[0]: {images[0].display_name}")
        return images[0].id
    
    raise Exception("❌ Không tìm thấy Ubuntu image cho ARM")


def check_instance_exists(compute_client, compartment_id):
    instances = compute_client.list_instances(
        compartment_id,
        display_name=INSTANCE_NAME,
    ).data
    
    for inst in instances:
        if inst.lifecycle_state not in ["TERMINATED", "TERMINATING"]:
            return True
    return False


def try_create_instance(compute_client, compartment_id, ad, image_id):
    """
    Trả về:
    - "SUCCESS"          : Tạo thành công
    - "NO_CAPACITY"      : Hết slot, thử AD khác
    - "RATE_LIMIT_STOP"  : Gặp 429, dừng hẳn để cron sau thử
    - "ERROR"            : Lỗi khác
    """
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
        print(f"\n{'='*60}")
        print(f"🎉🎉🎉 TẠO THÀNH CÔNG! 🎉🎉🎉")
        print(f"Instance OCID: {response.data.id}")
        print(f"Availability Domain: {ad}")
        print(f"{'='*60}\n")
        return "SUCCESS"
    
    except oci.exceptions.ServiceError as e:
        msg = str(e.message)
        
        if "Out of host capacity" in msg:
            print(f"⏳ AD {ad[-5:]}: Out of capacity")
            return "NO_CAPACITY"
        
        elif e.status == 429:
            print(f"⚠️  Rate limit (429). DỪNG để cron sau thử lại.")
            return "RATE_LIMIT_STOP"
        
        else:
            print(f"❌ Lỗi: {e.status} - {msg}")
            return "ERROR"
    
    except Exception as e:
        print(f"❌ Lỗi không xác định: {e}")
        return "ERROR"


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("OCI ARM Retry Script v4 FINAL")
    print(f"Instance: {INSTANCE_NAME}")
    print(f"Shape: {SHAPE} ({OCPUS} OCPU / {MEMORY_GB} GB RAM)")
    print(f"Boot Volume: {BOOT_VOLUME_GB} GB")
    print("=" * 60)
    
    # Kiểm tra env vars
    if not COMPARTMENT_ID:
        print("❌ Thiếu OCI_TENANCY.")
        sys.exit(1)
    if not SUBNET_ID:
        print("❌ Thiếu OCI_SUBNET_ID.")
        sys.exit(1)
    if "PASTE_PUBLIC_KEY" in SSH_PUBLIC_KEY:
        print("❌ CHƯA thay SSH_PUBLIC_KEY. Dừng lại.")
        sys.exit(1)
    
    # Load OCI config
    try:
        config = oci.config.from_file()
    except Exception as e:
        print(f"❌ Không đọc được OCI config: {e}")
        sys.exit(1)
    
    identity_client = oci.identity.IdentityClient(config)
    compute_client = oci.core.ComputeClient(config)
    
    # Kiểm tra instance đã tồn tại chưa
    if check_instance_exists(compute_client, COMPARTMENT_ID):
        print(f"✅ Instance '{INSTANCE_NAME}' đã tồn tại.")
        sys.exit(0)
    
    # Lấy danh sách AD
    ads = get_all_availability_domains(identity_client, COMPARTMENT_ID)
    print(f"\n📍 Có {len(ads)} Availability Domain(s):")
    for ad in ads:
        print(f"   - {ad}")
    
    # Lấy image
    try:
        image_id = get_ubuntu_image(compute_client, COMPARTMENT_ID)
        print(f"🖼️  Image ID: {image_id}\n")
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)
    
    # Retry loop
    for attempt in range(MAX_ATTEMPTS_PER_AD):
        print(f"━━━ Lượt thử #{attempt+1}/{MAX_ATTEMPTS_PER_AD} ━━━")
        
        for ad in ads:
            print(f"\n🎯 Thử AD: {ad[-15:]}")
            result = try_create_instance(compute_client, COMPARTMENT_ID, ad, image_id)
            
            if result == "SUCCESS":
                sys.exit(0)
            
            if result == "RATE_LIMIT_STOP":
                print("\n⚠️  Thoát để tránh 429 liên tục. Cron tiếp theo sẽ thử lại.")
                sys.exit(0)
            
            time.sleep(5)  # Nghỉ 5s giữa các AD
        
        if attempt < MAX_ATTEMPTS_PER_AD - 1:
            print(f"\n⏸️  Chờ {RETRY_INTERVAL}s...")
            time.sleep(RETRY_INTERVAL)
    
    print("\n" + "=" * 60)
    print("⏳ Hết lượt thử. GitHub Actions sẽ tự chạy lại sau 15 phút.")
    print("=" * 60)
    sys.exit(0)


if __name__ == "__main__":
    main()