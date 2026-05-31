"""
One-shot: patch 97 course_description chunks from authority_tier='community' to 'official'.
These were seeded before the seed_courses.py authority_tier fix (commit 46e33b9).
Run from repo root: python3 scripts/fix_course_desc_authority.py
"""
import asyncio
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values

IDS = [
    "26e1821d-1373-404e-b2b2-393bb387b5b7",
    "ab33333b-3379-4987-bd29-ba545da13b06",
    "b5566114-5c27-4f2a-9b37-2909ca97b666",
    "71639abf-ff93-4d4d-8199-e8e95deb0d61",
    "f2e1af23-ad1e-4c56-ac2b-b4ec947ecebb",
    "3d0f810f-a4c4-4004-8ecc-a25314ce8c25",
    "65690d8c-ea5d-4fa2-8554-21ec1225429d",
    "6d5f4364-3f14-46c4-b846-286ab2228fb9",
    "c8d61b65-4854-45b2-83ba-35493365bc8b",
    "8ce1ab65-38b5-46b9-bc90-14b2de11714c",
    "68ccb801-9bd2-49a8-9d1a-4dd7810d446e",
    "3ac41bfa-e620-47ab-be28-515b045ee8fd",
    "3aeb11a7-2f38-409a-acfa-f9c7b6d88151",
    "d8e7556d-69dc-43df-9414-429d739f7e1b",
    "c657e55a-4c3a-4838-94d3-6a1e01443551",
    "94468a48-688f-4af3-9cec-dc409b77a469",
    "52acd535-23ae-4f5e-a0b3-1c9584ec278f",
    "603760e9-e5f7-4b24-a609-b064aeed3e45",
    "9095afe1-9576-4af5-a87a-aee8c7b8e0b6",
    "de5f2978-7bf0-48e4-92c7-0e7a68cb98ff",
    "931fef2d-5192-47a0-b01d-2162d3f22691",
    "3b8cda51-7976-4618-adb0-d26d89c28fa2",
    "32fcb152-9e88-4c2c-a23d-e5eef93d090a",
    "cc5b9d53-9c6d-4a5c-b5e6-d9d3e1e67baf",
    "53b33a8b-e38d-46c0-94c0-24a82dcb9311",
    "9fc6d39e-6323-45ca-a3e0-74a3e04bb694",
    "7b9358f1-2d9a-491e-a597-c79916bed1b3",
    "31d99d55-8afb-4bd7-aafc-f1e802077f9d",
    "c7bc2ab8-fa53-43b8-9aa9-8cc53da02ed2",
    "95c382cf-61c6-403a-ba19-e7f0b57901a2",
    "e659a6d1-effc-40de-87be-1f99d1c7ec49",
    "ff199466-d541-4ce8-8c81-825c7e214fb8",
    "03a73ef1-bd17-49ce-a131-396c121f8244",
    "ad4d0ab0-e5a7-4b29-babe-2aede63ae650",
    "dbadd81e-9c22-44b8-b0b5-e84c59535a1d",
    "a6a92d27-abd1-438f-b9b4-fe198a76b1e3",
    "ba0c79cf-87ba-4798-98e9-cab2413b5520",
    "1b97a7d8-944a-4152-920d-2da28f90dfae",
    "4ed6f815-5852-49fa-96a1-77025ee2fd51",
    "d0fe48f2-37f2-48a3-a669-757c7242a258",
    "238705cb-28a9-46fe-abf8-2eccc1bebdfa",
    "f88e31e2-e33c-441d-9561-fc0e0d8b911b",
    "3f14e696-79b5-4049-a000-b23859befb33",
    "f0e164ad-a7ee-48b9-a42a-2bf988dd5806",
    "1e0a5e13-58ec-492d-8f3d-c98009c9ca21",
    "2048a995-685e-4ce6-b551-cf2d8fb8bb4d",
    "0837d7b0-6ce5-4c43-a5e7-868d18983c80",
    "f27c9ad3-e728-46fd-933c-a25d9b1d5db1",
    "79e3f9a2-7d88-4f43-9e65-4c0a7b8a596e",
    "e5e83bdd-430f-408f-9782-bc0c8f94ad2a",
    "d0d8304c-cda7-4fa1-abd0-d9910c7c52f6",
    "e49bb2c7-70ec-48cd-8c1b-435a06d4f442",
    "85ebbed2-b589-4833-af80-1dca94e8e142",
    "9d827e4b-d88e-4051-b62b-3022645700bb",
    "8634bf3f-5d70-41bf-9fcd-f1b2bc8ddb70",
    "e7b39ca4-3a68-49c7-9264-1482c298b15d",
    "178fe8fd-5d59-4d5c-9552-c13c45eace62",
    "78201aa9-390b-47f9-93cf-8345525d7b68",
    "058aaf62-01e7-41c4-8ddc-f87628312934",
    "c3aae2dc-4d73-4a36-b11d-3701bf3dc241",
    "5314734e-6d00-4c21-a9b1-3e4c00bb87ce",
    "af3eb855-94f1-487f-b031-a7cea470056a",
    "d77bbe54-d456-4a5e-a210-f79f784a10ee",
    "6600de29-5217-4a87-b8df-6a45013d9949",
    "83e3d91a-1c4d-48b6-82f0-910862ff5006",
    "576937af-a9f9-4d13-87eb-e83fabc52a42",
    "2e5e69d5-b76b-43c3-88d0-579c9d82fe29",
    "c4bd54a5-af79-442e-9db1-ee7cd4657cc6",
    "a647ace0-d2c2-4537-aa92-5759e9cc3d5a",
    "d9b1afa2-103e-480f-b7fc-dddaa9c76afe",
    "81a80d59-b79e-41e2-8a98-275babdf8db7",
    "5d085f8b-9859-4cac-ac54-37e347912eb5",
    "84aa1459-edf6-4a6f-b1b6-a9edc00c80fc",
    "c48c49c3-25b1-403a-8951-966ee8d1aad3",
    "7feba29a-fd87-48e5-8631-ab964c86567a",
    "74b64d50-893d-4756-a9ab-dc6a2a99248b",
    "20e3ed46-a016-4e8f-8c01-4756cf759434",
    "b0d6a66f-9368-4a36-aa43-5097dc1bddf3",
    "73f28232-5747-41a5-b81c-ce3950cde8e0",
    "bf8b8b49-978d-4ddf-bfe3-db142aee708a",
    "6e93fbe3-a529-4329-9f34-05df5954cff0",
    "11bbe31d-7d69-493d-9534-53c8173a11b9",
    "91f0d000-f9a5-484c-b3db-d509b7322b9a",
    "03ad96c0-0846-43dd-b12d-c82e5ceedf09",
    "477ee27b-5a0e-4f02-894f-3815b6dcb77c",
    "acb55f08-02eb-4984-904a-2bf29715fa6d",
    "bbdc998f-1998-4243-a093-326fea312327",
    "a3b4d28f-16f7-4477-b93a-1d471f138cfa",
    "ec1f5a1d-4ff5-4ded-9278-340d4d31c615",
    "3f31bdd6-4acf-44fa-9898-abb182ec25d2",
    "cf95f938-b11e-4f38-a96f-526b22b3a131",
    "eef94f17-ebf9-4b27-8215-6edb452213dc",
    "f3c9bd28-dabb-494a-a263-02583df5a75c",
    "44503582-e6ee-4cb5-aeb6-9240cdd44e4f",
    "93e70a47-d192-4177-a42d-4cbcb7cf6a83",
    "547b5a74-53ef-4edd-aa4a-e11863ba4842",
    "05687a64-5590-447b-a466-c13e5794ade4",
]

BATCH = 10


async def main():
    env = dotenv_values(dotenv_path=Path(__file__).parent.parent / ".env")
    url = env.get("SUPABASE_URL")
    key = env.get("SUPABASE_KEY")
    if not url or not key:
        raise SystemExit("Missing SUPABASE_URL or SUPABASE_KEY in .env")

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    patched = 0
    async with httpx.AsyncClient(timeout=30) as http:
        for i in range(0, len(IDS), BATCH):
            batch = IDS[i : i + BATCH]
            id_filter = f"in.({','.join(batch)})"
            r = await http.patch(
                f"{url}/rest/v1/document_chunks?id={id_filter}",
                headers=headers,
                json={"authority_tier": "official"},
            )
            if r.status_code not in (200, 204):
                print(f"BATCH_ERROR | i={i} | status={r.status_code} | {r.text[:200]}")
            else:
                patched += len(batch)
                print(f"PATCHED | batch={i//BATCH + 1} | rows={len(batch)} | total={patched}")

        # verify
        r = await http.get(
            f"{url}/rest/v1/document_chunks"
            "?source_type=eq.course_description&authority_tier=eq.community&select=id",
            headers={**headers, "Prefer": "count=exact"},
        )
        remaining = int(r.headers.get("content-range", "0/0").split("/")[-1])
        print(f"VERIFY | remaining_community={remaining} (expect 0)")

    print(f"DONE | total_patched={patched}/{len(IDS)}")


if __name__ == "__main__":
    asyncio.run(main())
