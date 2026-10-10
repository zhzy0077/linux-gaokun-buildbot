// SPDX-License-Identifier: GPL-2.0-only
/*
 * ucsi-huawei-gaokun - A UCSI driver for HUAWEI Matebook E Go
 *
 * Copyright (C) 2024-2025 Pengyu Luo <mitltlatltl@gmail.com>
 */

#include <drm/bridge/aux-bridge.h>
#include <linux/auxiliary_bus.h>
#include <linux/bitops.h>
#include <linux/container_of.h>
#include <linux/module.h>
#include <linux/notifier.h>
#include <linux/of.h>
#include <linux/platform_data/huawei-gaokun-ec.h>
#include <linux/string.h>
#include <linux/usb/pd_vdo.h>
#include <linux/usb/typec_altmode.h>
#include <linux/usb/typec_dp.h>
#include <linux/usb/typec_mux.h>
#include <linux/workqueue_types.h>

#include "ucsi.h"

#define EC_EVENT_UCSI	0x21
#define EC_EVENT_USB	0x22

#define GAOKUN_CCX_MASK		GENMASK(1, 0)
#define GAOKUN_MUX_MASK		GENMASK(3, 2)

#define GAOKUN_DPAM_MASK	GENMASK(3, 0)
#define GAOKUN_HPD_STATE_MASK	BIT(4)
#define GAOKUN_HPD_IRQ_MASK	BIT(5)

#define GAOKUN_UCSI_MAX_PORTS	2
#define GAOKUN_UCSI_REGISTER_DELAY	(3 * HZ)
#define GAOKUN_UCSI_RETRY_DELAY		(10 * HZ)
#define GAOKUN_UCSI_MAX_RETRIES		3

#define CCX_TO_ORI(ccx) (++(ccx) % 3) /* convert ccx to enum typec_orientation */

/* Configuration Channel Extension */
enum gaokun_ucsi_ccx {
	USBC_CCX_NORMAL,
	USBC_CCX_REVERSE,
	USBC_CCX_NONE,
};

enum gaokun_ucsi_mux {
	USBC_MUX_NONE,
	USBC_MUX_USB_2L,
	USBC_MUX_DP_4L,
	USBC_MUX_USB_DP,
};

/* based on pmic_glink_altmode_pin_assignment */
enum gaokun_ucsi_dpam_pan {	/* DP Alt Mode Pin Assignments */
	USBC_DPAM_PAN_NONE,
	USBC_DPAM_PAN_A,	/* Not supported after USB Type-C Standard v1.0b */
	USBC_DPAM_PAN_B,	/* Not supported after USB Type-C Standard v1.0b */
	USBC_DPAM_PAN_C,	/* USBC_DPAM_PAN_C_REVERSE - 6 */
	USBC_DPAM_PAN_D,
	USBC_DPAM_PAN_E,
	USBC_DPAM_PAN_F,	/* Not supported after USB Type-C Standard v1.0b */
	USBC_DPAM_PAN_A_REVERSE,/* Not supported after USB Type-C Standard v1.0b */
	USBC_DPAM_PAN_B_REVERSE,/* Not supported after USB Type-C Standard v1.0b */
	USBC_DPAM_PAN_C_REVERSE,
	USBC_DPAM_PAN_D_REVERSE,
	USBC_DPAM_PAN_E_REVERSE,
	USBC_DPAM_PAN_F_REVERSE,/* Not supported after USB Type-C Standard v1.0b */
};

struct gaokun_ucsi_reg {
	u8 num_ports;
	u8 port_updt;
	u8 port_data[4];
	u8 checksum;
	u8 reserved;
} __packed;

struct gaokun_ucsi_port {
	spinlock_t lock; /* serializing port resource access */

	struct gaokun_ucsi *ucsi;
	struct auxiliary_device *bridge;

	struct typec_mux *typec_mux;
	struct typec_mux_state state;
	struct typec_altmode dp_alt;

	int idx;
	enum gaokun_ucsi_ccx ccx;
	enum gaokun_ucsi_mux mux;
	u8 mode;
	u16 svid;
	u8 hpd_state;
	u8 hpd_irq;
	bool applied;
	enum gaokun_ucsi_ccx applied_ccx;
	u16 applied_svid;
	u8 applied_mode;
	u8 applied_hpd;
};

struct gaokun_ucsi {
	struct gaokun_ec *ec;
	struct ucsi *ucsi;
	struct gaokun_ucsi_port *ports;
	struct device *dev;
	struct delayed_work work;
	struct delayed_work sync_work;
	struct notifier_block nb;
	u16 version;
	u8 num_ports;
	u8 retries;
	bool registered;
	bool notifier_registered;
	bool ready;
};

/* -------------------------------------------------------------------------- */
/* For UCSI */

static int gaokun_ucsi_read_version(struct ucsi *ucsi, u16 *version)
{
	struct gaokun_ucsi *uec = ucsi_get_drvdata(ucsi);

	*version = uec->version;

	return 0;
}

static int gaokun_ucsi_read_cci(struct ucsi *ucsi, u32 *cci)
{
	struct gaokun_ucsi *uec = ucsi_get_drvdata(ucsi);
	u8 buf[GAOKUN_UCSI_READ_SIZE];
	int ret;

	ret = gaokun_ec_ucsi_read(uec->ec, buf);
	if (ret)
		return ret;

	memcpy(cci, buf, sizeof(*cci));

	return 0;
}

static int gaokun_ucsi_read_message_in(struct ucsi *ucsi,
				       void *val, size_t val_len)
{
	struct gaokun_ucsi *uec = ucsi_get_drvdata(ucsi);
	u8 buf[GAOKUN_UCSI_READ_SIZE];
	int ret;

	ret = gaokun_ec_ucsi_read(uec->ec, buf);
	if (ret)
		return ret;

	memcpy(val, buf + GAOKUN_UCSI_CCI_SIZE,
	       min(val_len, GAOKUN_UCSI_MSGI_SIZE));

	return 0;
}

static int gaokun_ucsi_async_control(struct ucsi *ucsi, u64 command)
{
	struct gaokun_ucsi *uec = ucsi_get_drvdata(ucsi);
	u8 buf[GAOKUN_UCSI_WRITE_SIZE] = {};

	memcpy(buf, &command, sizeof(command));

	return gaokun_ec_ucsi_write(uec->ec, buf);
}

static void gaokun_ucsi_update_connector(struct ucsi_connector *con)
{
	struct gaokun_ucsi *uec = ucsi_get_drvdata(con->ucsi);

	if (con->num > uec->num_ports)
		return;

	con->typec_cap.orientation_aware = true;
}

static void gaokun_set_orientation(struct ucsi_connector *con,
				   struct gaokun_ucsi_port *port)
{
	enum gaokun_ucsi_ccx ccx;
	unsigned long flags;

	spin_lock_irqsave(&port->lock, flags);
	ccx = port->ccx;
	spin_unlock_irqrestore(&port->lock, flags);

	typec_set_orientation(con->port, CCX_TO_ORI(ccx));
}

static void gaokun_ucsi_connector_status(struct ucsi_connector *con)
{
	struct gaokun_ucsi *uec = ucsi_get_drvdata(con->ucsi);
	struct gaokun_ucsi_reg reg;
	unsigned long flags;
	int idx;

	idx = con->num - 1;
	if (con->num > uec->num_ports) {
		dev_warn(uec->dev, "set orientation out of range: con%d\n", idx);
		return;
	}

	/* Read the current orientation before the core publishes this status. */
	mutex_lock(&con->ucsi->ppm_lock);
	if (!gaokun_ec_ucsi_get_reg(uec->ec, &reg) &&
	    reg.num_ports == uec->num_ports) {
		spin_lock_irqsave(&uec->ports[idx].lock, flags);
		uec->ports[idx].ccx = FIELD_GET(GAOKUN_CCX_MASK,
					     reg.port_data[idx * 2]);
		spin_unlock_irqrestore(&uec->ports[idx].lock, flags);
	}
	gaokun_set_orientation(con, &uec->ports[idx]);
	mutex_unlock(&con->ucsi->ppm_lock);
}

static const struct ucsi_operations gaokun_ucsi_ops = {
	.read_version = gaokun_ucsi_read_version,
	.read_cci = gaokun_ucsi_read_cci,
	.poll_cci = gaokun_ucsi_read_cci,
	.read_message_in = gaokun_ucsi_read_message_in,
	.sync_control = ucsi_sync_control_common,
	.async_control = gaokun_ucsi_async_control,
	.update_connector = gaokun_ucsi_update_connector,
	.connector_status = gaokun_ucsi_connector_status,
};

/* -------------------------------------------------------------------------- */
/* For Altmode */

static void gaokun_ucsi_port_update(struct gaokun_ucsi_port *port,
				    const u8 *port_data)
{
	struct gaokun_ucsi *uec = port->ucsi;
	int offset = port->idx * 2; /* every port has 2 Bytes data */
	unsigned long flags;
	u8 dcc, ddi;

	dcc = port_data[offset];
	ddi = port_data[offset + 1];

	spin_lock_irqsave(&port->lock, flags);

	port->ccx = FIELD_GET(GAOKUN_CCX_MASK, dcc);
	port->mux = FIELD_GET(GAOKUN_MUX_MASK, dcc);
	port->mode = FIELD_GET(GAOKUN_DPAM_MASK, ddi);
	port->hpd_state = FIELD_GET(GAOKUN_HPD_STATE_MASK, ddi);
	port->hpd_irq = FIELD_GET(GAOKUN_HPD_IRQ_MASK, ddi);

	switch (port->mode) {
	case USBC_DPAM_PAN_C:
	case USBC_DPAM_PAN_C_REVERSE:
		port->mode = TYPEC_DP_STATE_C; /* correct it for usb later */
		break;
	case USBC_DPAM_PAN_D:
	case USBC_DPAM_PAN_D_REVERSE:
		port->mode = TYPEC_DP_STATE_D;
		break;
	case USBC_DPAM_PAN_E:
	case USBC_DPAM_PAN_E_REVERSE:
		port->mode = TYPEC_DP_STATE_E;
		break;
	case USBC_DPAM_PAN_NONE:
		port->mode = TYPEC_STATE_SAFE;
		break;
	default:
		dev_warn(uec->dev, "unknown mode %d\n", port->mode);
		break;
	}

	switch (port->mux) {
	case USBC_MUX_NONE:
		port->svid = 0;
		break;
	case USBC_MUX_USB_2L:
		port->svid = USB_SID_PD;
		port->mode = TYPEC_STATE_USB; /* same as PAN_C, correct it */
		break;
	case USBC_MUX_DP_4L:
	case USBC_MUX_USB_DP:
		port->svid = USB_SID_DISPLAYPORT;
		break;
	default:
		dev_warn(uec->dev, "unknown mux state %d\n", port->mux);
		break;
	}

	spin_unlock_irqrestore(&port->lock, flags);
}

/*
 * All sideband I/O and mux updates run in workqueue context under ppm_lock.
 * The EC IRQ thread must remain available to complete UCSI commands.
 */
static int gaokun_ucsi_apply_port(struct gaokun_ucsi_port *port)
{
	struct gaokun_ucsi *uec = port->ucsi;
	bool dp = port->svid == USB_SID_DISPLAYPORT;
	bool hpd = dp && port->hpd_state;
	int ret;

	gaokun_set_orientation(&uec->ucsi->connector[port->idx], port);

	if (!port->applied || port->applied_ccx != port->ccx ||
	    port->applied_svid != port->svid || port->applied_mode != port->mode) {
		/* Drop HPD before taking lanes away from a live DP connection. */
		if (port->applied_hpd && port->bridge)
			drm_aux_hpd_bridge_notify(&port->bridge->dev,
						 connector_status_disconnected);

		port->dp_alt.svid = port->svid;
		port->state.mode = port->mode;
		port->state.alt = &port->dp_alt;
		ret = typec_mux_set(port->typec_mux, &port->state);
		if (ret) {
			port->applied = false;
			return ret;
		}
		port->applied_hpd = false;
	}

	if (port->bridge && (!port->applied || port->applied_hpd != hpd ||
			     (hpd && port->hpd_irq)))
		drm_aux_hpd_bridge_notify(&port->bridge->dev,
					 hpd ? connector_status_connected :
					 connector_status_disconnected);

	port->applied = true;
	port->applied_ccx = port->ccx;
	port->applied_svid = port->svid;
	port->applied_mode = port->mode;
	port->applied_hpd = hpd;

	return 0;
}

static void gaokun_ucsi_sync_worker(struct work_struct *work)
{
	struct gaokun_ucsi *uec = container_of(to_delayed_work(work),
					     struct gaokun_ucsi, sync_work);
	struct gaokun_ucsi_reg reg;
	bool retry = false;
	int i, ret;

	mutex_lock(&uec->ucsi->ppm_lock);
	ret = gaokun_ec_ucsi_get_reg(uec->ec, &reg);
	if (ret || reg.num_ports != uec->num_ports) {
		retry = true;
		goto out;
	}

	/*
	 * port_updt is an ACK mask, not a connected-port bitmap. A monitor
	 * present at boot can have HPD=1 with only the other port pending.
	 * Always consume the complete snapshot, including disconnected ports.
	 */
	for (i = 0; i < uec->num_ports; i++) {
		gaokun_ucsi_port_update(&uec->ports[i], reg.port_data);
		/* Replay level state, but deliver IRQ pulses only for new events. */
		if (!(reg.port_updt & BIT(i)))
			uec->ports[i].hpd_irq = 0;
		/* Pair with ready publication after the core work has finished. */
		if (smp_load_acquire(&uec->ready)) {
			ret = gaokun_ucsi_apply_port(&uec->ports[i]);
			if (ret) {
				retry = true;
				continue;
			}
		}

		/* Drain early events too; registration will replay every port. */
		if (reg.port_updt & BIT(i)) {
			ret = gaokun_ec_ucsi_pan_ack(uec->ec, i);
			if (ret)
				retry = true;
		}
	}
	if (!reg.port_updt && gaokun_ec_ucsi_pan_ack(uec->ec,
						   GAOKUN_UCSI_NO_PORT_UPDATE))
		retry = true;
out:
	mutex_unlock(&uec->ucsi->ppm_lock);
	if (retry)
		queue_delayed_work(system_wq, &uec->sync_work, HZ);
}

static int gaokun_ucsi_notify(struct notifier_block *nb,
			    unsigned long action, void *data)
{
	struct gaokun_ucsi *uec = container_of(nb, struct gaokun_ucsi, nb);
	u32 cci;

	switch (action) {
	case EC_EVENT_USB:
		mod_delayed_work(system_wq, &uec->sync_work, 0);
		return NOTIFY_OK;
	case EC_EVENT_UCSI:
		if (gaokun_ucsi_read_cci(uec->ucsi, &cci))
			return NOTIFY_DONE;

		ucsi_notify_common(uec->ucsi, cci);
		/*
		 * USB events normally follow connector changes. If missing,
		 * recover asynchronously; never wait in the EC IRQ notifier.
		 * queue (not mod) keeps repeated CCIs from postponing recovery.
		 */
		if (UCSI_CCI_CONNECTOR(cci) &&
		    UCSI_CCI_CONNECTOR(cci) <= uec->num_ports)
			queue_delayed_work(system_wq, &uec->sync_work, 2 * HZ);
		return NOTIFY_OK;
	default:
		return NOTIFY_DONE;
	}
}

static void gaokun_ucsi_put_mux(void *mux)
{
	typec_mux_put(mux);
}

static int gaokun_ucsi_ports_init(struct gaokun_ucsi *uec)
{
	struct gaokun_ucsi_port *ucsi_port;
	struct device *dev = uec->dev;
	struct fwnode_handle *fwnode;
	int i, ret, num_ports;
	u32 port;

	/* Build the DRM graph without early EC traffic; initialize UCSI later. */
	num_ports = device_get_child_node_count(dev);
	if (!num_ports || num_ports > GAOKUN_UCSI_MAX_PORTS)
		return -EINVAL;
	uec->num_ports = num_ports;
	uec->ports = devm_kcalloc(dev, num_ports, sizeof(*uec->ports),
				  GFP_KERNEL);
	if (!uec->ports)
		return -ENOMEM;

	for (i = 0; i < num_ports; ++i) {
		ucsi_port = &uec->ports[i];
		ucsi_port->ccx = USBC_CCX_NONE;
		ucsi_port->idx = i;
		ucsi_port->ucsi = uec;
		spin_lock_init(&ucsi_port->lock);
	}

	device_for_each_child_node(dev, fwnode) {
		ret = fwnode_property_read_u32(fwnode, "reg", &port);
		if (ret < 0) {
			dev_err(dev, "missing reg property of %pOFn\n", fwnode);
			fwnode_handle_put(fwnode);
			return ret;
		}

		if (port >= num_ports || uec->ports[port].bridge) {
			fwnode_handle_put(fwnode);
			return -EINVAL;
		}

		ucsi_port = &uec->ports[port];
		ucsi_port->bridge = devm_drm_dp_hpd_bridge_alloc(dev, to_of_node(fwnode));
		if (IS_ERR(ucsi_port->bridge)) {
			fwnode_handle_put(fwnode);
			return PTR_ERR(ucsi_port->bridge);
		}

		ucsi_port->typec_mux = fwnode_typec_mux_get(fwnode);
		if (IS_ERR(ucsi_port->typec_mux)) {
			fwnode_handle_put(fwnode);
			return dev_err_probe(dev, PTR_ERR(ucsi_port->typec_mux),
					     "failed to acquire mode-switch for port: %d\n",
					     port);
		}
		ret = devm_add_action_or_reset(dev, gaokun_ucsi_put_mux,
					      ucsi_port->typec_mux);
		if (ret) {
			fwnode_handle_put(fwnode);
			return ret;
		}
	}
	for (i = 0; i < num_ports; i++) {
		if (!uec->ports[i].bridge)
			continue;

		ret = devm_drm_dp_hpd_bridge_add(dev, uec->ports[i].bridge);
		if (ret)
			return ret;
	}

	return 0;
}

static void gaokun_ucsi_register_worker(struct work_struct *work)
{
	struct gaokun_ucsi *uec = container_of(to_delayed_work(work),
					     struct gaokun_ucsi, work);
	struct ucsi *ucsi = uec->ucsi;
	int ret;

	if (!uec->notifier_registered) {
		ret = gaokun_ec_register_notify(uec->ec, &uec->nb);
		if (ret)
			goto retry;
		uec->notifier_registered = true;
	}

	if (!uec->registered) {
		ret = ucsi_register(ucsi);
		if (ret)
			goto retry;
		uec->registered = true;
	}

	/*
	 * ucsi_register() only queues initialization. Wait for that work, not
	 * an arbitrary delay, before dereferencing connector[]. No locks are
	 * held here, so the notifier and sideband worker can make progress.
	 */
	flush_delayed_work(&ucsi->work);
	if (delayed_work_pending(&ucsi->work)) {
		/* The core is still waiting for a role-switch supplier. */
		schedule_delayed_work(&uec->work, GAOKUN_UCSI_REGISTER_DELAY);
		return;
	}

	if (!ucsi->connector) {
		/*
		 * Retry only the UCSI core. Reprobing the auxiliary device would
		 * unplug the HPD bridges already attached to the DRM encoder.
		 */
		mutex_lock(&ucsi->ppm_lock);
		ucsi_unregister(ucsi);
		mutex_unlock(&ucsi->ppm_lock);
		uec->registered = false;
		ret = -ETIMEDOUT;
		goto retry;
	}

	if (ucsi->cap.num_connectors != uec->num_ports) {
		dev_err(uec->dev, "UCSI/DT connector count mismatch: %u/%u\n",
			ucsi->cap.num_connectors, uec->num_ports);
		return;
	}

	/* Publish fully initialized connector[] to the sideband worker. */
	smp_store_release(&uec->ready, true);
	mod_delayed_work(system_wq, &uec->sync_work, 0);
	return;

retry:
	if (uec->retries++ >= GAOKUN_UCSI_MAX_RETRIES) {
		dev_err(uec->dev, "UCSI initialization failed after retries: %d\n",
			ret);
		return;
	}

	dev_warn(uec->dev, "retrying UCSI initialization in 10 seconds: %d\n",
		 ret);
	schedule_delayed_work(&uec->work, GAOKUN_UCSI_RETRY_DELAY);
}

static int gaokun_ucsi_probe(struct auxiliary_device *adev,
			     const struct auxiliary_device_id *id)
{
	struct gaokun_ec *ec = adev->dev.platform_data;
	struct device *dev = &adev->dev;
	struct gaokun_ucsi *uec;
	int ret;

	uec = devm_kzalloc(dev, sizeof(*uec), GFP_KERNEL);
	if (!uec)
		return -ENOMEM;

	uec->ec = ec;
	uec->dev = dev;
	uec->version = UCSI_VERSION_1_0;
	uec->nb.notifier_call = gaokun_ucsi_notify;

	INIT_DELAYED_WORK(&uec->work, gaokun_ucsi_register_worker);
	INIT_DELAYED_WORK(&uec->sync_work, gaokun_ucsi_sync_worker);

	ret = gaokun_ucsi_ports_init(uec);
	if (ret)
		return ret;

	uec->ucsi = ucsi_create(dev, &gaokun_ucsi_ops);
	if (IS_ERR(uec->ucsi))
		return PTR_ERR(uec->ucsi);

	ucsi_set_drvdata(uec->ucsi, uec);
	auxiliary_set_drvdata(adev, uec);

	/* EC can't handle UCSI properly in the early stage */
	schedule_delayed_work(&uec->work, GAOKUN_UCSI_REGISTER_DELAY);

	return 0;
}

static void gaokun_ucsi_remove(struct auxiliary_device *adev)
{
	struct gaokun_ucsi *uec = auxiliary_get_drvdata(adev);

	disable_delayed_work_sync(&uec->work);
	if (uec->notifier_registered)
		gaokun_ec_unregister_notify(uec->ec, &uec->nb);
	disable_delayed_work_sync(&uec->sync_work);
	if (uec->registered)
		ucsi_unregister(uec->ucsi);

	ucsi_destroy(uec->ucsi);
}

static const struct auxiliary_device_id gaokun_ucsi_id_table[] = {
	{ .name = GAOKUN_MOD_NAME "." GAOKUN_DEV_UCSI, },
	{}
};
MODULE_DEVICE_TABLE(auxiliary, gaokun_ucsi_id_table);

static struct auxiliary_driver gaokun_ucsi_driver = {
	.name = GAOKUN_DEV_UCSI,
	.id_table = gaokun_ucsi_id_table,
	.probe = gaokun_ucsi_probe,
	.remove = gaokun_ucsi_remove,
};

module_auxiliary_driver(gaokun_ucsi_driver);

MODULE_DESCRIPTION("HUAWEI Matebook E Go UCSI driver");
MODULE_LICENSE("GPL");
