# immich-backup-albums-to-external-lib

## Introduction

[Immich](https://immich.app/) is a great open source self-hosted image and video management solution. Nevertheless, if you already have
an existing manually managed image/video archive, you might be reluctant to let immich organize the files on the file system according 
to immich-internal structures. This might also be an unwanted tool lock-in.

Fortunately, immich offers the integration of external libraries which are located in the file system. 

But if you use external libraries, you have to organize the images and videos manually by e.g. creating own folders for each year or event.

This repository contains a tool which allows to benefit from using the immich functionality of organizing images/videos in albums but still continue to
have the assets in an external library.

The proposed workflow is as follows:

* the immich mobile app is used to upload photos from the smartphone to the immich server
* in the immich app, you organize the photos by adding them to albums
* when an album is complete, you use the immich-backup-albums-to-external-lib tool to automatically copy the album to your external library folder
* optionally, the tool deletes the assets from immich's internal storage and database and deletes the album
* you can use [immich-folder-album-creator](https://github.com/Salvoxia/immich-folder-album-creator) to automatically re-create the album from the 
  external library

## Installation

## Usage

## Security Considerations

Don't use on external webservers.
It is recommended to use it only on localhost or a server which is not reachable from the Internet.