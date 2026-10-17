English | [한국어](README.ko.md)

# Petalpoint · Tailpoint

Two Windows mouse cursor packs. Each pack covers all 17 Windows pointer roles, each file holds five sizes, and you install with a right-click.

## Packs

### Petalpoint
![Petalpoint preview](design/b-pastel.png)

A soft lavender body with a dark indigo outline. Inspired by Material You's tonal palette. The arrow is a tail-less dart, Busy is a dark purple arc on a lavender ring, and Normal and Link breathe gently.

### Tailpoint
![Tailpoint preview](design/d-capsule.png)

A white arrowhead with a detached lime capsule trailing behind it. The lime part shows the state: a "?" capsule for Help, lime dots in a white capsule for Busy, and click marks on a lime arrowhead for Link.

## Features

- All 17 Windows cursor roles in each pack.
- Each file holds 32, 48, 64, 96 and 128 px images, so Windows can pick a sharp size for your display scaling.
- Busy and Working in Background spin. Normal and Link breathe slowly. The rest are still.
- Install and uninstall with a right-click on an `.inf` file.
- Windows 10 and 11.

## Install

1. Download the zip for the pack you want (`petalpoint.zip` or `tailpoint.zip`) from the [latest release](https://github.com/spilak0203-debug/petalpoint-tailpoint/releases/latest) and **extract it**. You get one folder: `petalpoint\` or `tailpoint\`.
2. Open that folder, **right-click `install.inf`**, and choose **Install**. If you don't see it, choose **Show more options** first. Windows asks for administrator rights, because the cursor files are copied to `C:\Windows\Cursors\<Name>`. Click **Yes**.
3. Open **Settings → Bluetooth & devices → Mouse → Additional mouse settings → Pointers**. The settings window does not open by itself after install. Choose **Petalpoint** or **Tailpoint** in the Scheme list, then press **Apply**.

> **Caveats**
> - Install from the account you will actually use. If you are a standard user and enter another admin's password at the prompt, the scheme is registered under that admin's account, not yours.
> - Installing overwrites your current cursor settings without a backup. Note your current scheme first if you want to go back.

## Uninstall

Keep the extracted folder: you need its `uninstall.inf` to remove the pack.

1. In the extracted folder, **right-click `uninstall.inf`** and choose **Install**. This deletes the 17 cursor files and the scheme entry, and switches your cursors back to **Windows Default**. It does this even if you are using a different scheme at that moment, so pick that scheme again in **Pointers** afterwards.
2. The change shows after you sign out and back in, or after you open **Pointers** and press **Apply**.
3. If a cursor file is in use, Windows may ask you to restart. An empty folder `C:\Windows\Cursors\<Name>` stays behind. Delete it by hand if you want.

## Troubleshooting

- **The cursor didn't change.** Open **Pointers**, choose the scheme, and press **Apply**.
- **Installing from inside the zip failed.** Extract the zip first, then install from the extracted folder.
- **Blurry at my display scaling.** Windows picks one of the five sizes for your scaling. The **Pointer size** setting (Settings → Bluetooth & devices → Mouse) also matters, so check it too.

## License

MIT License. Copyright (c) 2026 spilak0203. See [LICENSE](LICENSE).

Not affiliated with or endorsed by Google. Other names belong to their respective owners.
