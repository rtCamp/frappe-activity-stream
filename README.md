<div align="center">
<h2>Frappe Activity Stream</h2>
   Frappe app designed to track and store user activity across the system.
</div>
<br>
<div align="center">
</div>

## Key Features

- **Comprehensive Activity Logging**: Track all major user activities including document creation, updates, deletions, submissions, cancellations, as well as user logins and logouts.
- **Configurable Record Retention**: Control how long activity records are stored with options to keep them indefinitely or for a specified number of days.
- **Event Selection**: Select only the specific DocTypes or event types to be logged to prevent unnecessary data collection and reduce database size.
- **Track Changes**: For create, update, and delete events, the app provides a table showing exactly which values were changed.
- **Track Event Source**: The app tracks what event caused this activity, whether it was an API call or a background job, and stores the relevant method and arguments.
- **Mask Sensitive Fields**: In the app's settings, you can define specific keys (e.g., `pwd`, `password`, `secret`, `token`, `api_key`, `access_token`) whose values will be masked in the activity log to protect sensitive data.
- **Simple, Plug-and-Play Integration**: The app works out-of-the-box with any Frappe site by leveraging core event hooks, requiring minimal setup.



## Installation

Run the following command to install the app.

```bash
bench get-app git@github.com:rtCamp/frappe-activity-stream.git
bench --site [site-name] install-app frappe_activity_stream
bench --site [site-name] migrate
bench restart
```

For local development, check out our dev-tool for seamlessly building Frappe apps: [frappe-manager](https://github.com/rtCamp/Frappe-Manager)  
NOTE: If using `frappe-manager`, you might require to `fm restart` to provision the worker queues.

## System Setup

### After installation, the app is ready to track activities. To customize its behavior, you can configure the `Activity Stream Settings` Single DocType.

### Configuring Activity Stream Settings

1. **Access Settings**: Go to the Awesome Bar in your Frappe site and type "Activity Stream Settings" to open the DocType.

2. **Record Retention**:
   - **Keep Records Indefinitely**: Check this box if you want to keep all activity records without purging them.
   - **Keep Records For (Days)**: If `Keep Records Indefinitely` is unchecked, specify the number of days to retain activity logs. Any logs older than this duration will be automatically purged by a scheduled background job.

3. **Include List under Rules tab**:
   - To start activity stream, setup doctypes using the `Include List` table.
   - Click the "Add Row" button.
   - Select the **DocType** (e.g., `User`, `Login Log`) you want to include.
   - Select the **Event Type** (e.g., `Update`, `Login`, or `All`) that you want to track for that doctype.
   - You can also include all doctypes under a module by clicking `Import from Module` button under the table.
   - It will open a dialogue where you can select the module and the event type, and once you click import, all the doctypes for that module will be imported.

4. **Mask Sensitive Fields**:
   - To protect sensitive data from being logged, use the `Sensitive Fields` table.
   - Under the text box, enter the fieldname of the keys you wish to mask seperated by commas. The values for these fields will be replaced with a placeholder (e.g., `********`) in the API/Background Job Args and in the Values Changed table.


## Contribution Guide

Please read [contribution.md](./CONTRIBUTING.md) for details.

## License

This project is licensed under the [AGPLv3 License](./LICENSE).
