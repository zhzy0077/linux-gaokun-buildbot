/* -*- mode: C; c-file-style: "gnu"; indent-tabs-mode: nil; -*- */

/*
 * Copyright (C) 2013-2017 Red Hat
 * Copyright (C) 2018 DisplayLink (UK) Ltd.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License as
 * published by the Free Software Foundation; either version 2 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, see <http://www.gnu.org/licenses/>.
 */

/* Selection functions from Mutter 50.5, commit
 * c28623c8d29c7b61ec744cdfdbec36f5e7faef1c. Kept verbatim for native tests.
 */
static gboolean
is_plane_assigned (MetaKmsPlane     *plane,
                   MetaKmsPlaneType  plane_type,
                   GPtrArray        *crtc_assignments)
{
  size_t i;

  for (i = 0; i < crtc_assignments->len; i++)
    {
      MetaCrtcAssignment *assigned_crtc_assignment =
        g_ptr_array_index (crtc_assignments, i);
      CrtcKmsAssignment *kms_assignment;

      if (!META_IS_CRTC_KMS (assigned_crtc_assignment->crtc))
        continue;

      kms_assignment = assigned_crtc_assignment->backend_private;
      switch (plane_type)
        {
        case META_KMS_PLANE_TYPE_PRIMARY:
          if (kms_assignment->primary_plane == plane)
            return TRUE;
          break;
        case META_KMS_PLANE_TYPE_CURSOR:
          if (kms_assignment->cursor_plane == plane)
            return TRUE;
          break;
        case META_KMS_PLANE_TYPE_OVERLAY:
          g_assert_not_reached ();
        }
    }

  return FALSE;
}

static MetaKmsPlane *
crtc_assigned_plane (MetaCrtcKms      *crtc_kms,
                     MetaKmsPlaneType  kms_plane_type)
{
  switch (kms_plane_type)
    {
    case META_KMS_PLANE_TYPE_PRIMARY:
      return crtc_kms->assigned_primary_plane;
    case META_KMS_PLANE_TYPE_CURSOR:
      return crtc_kms->assigned_cursor_plane;
    case META_KMS_PLANE_TYPE_OVERLAY:
    default:
      g_assert_not_reached ();
    }
}

static gboolean
is_plane_active (MetaKmsDevice *kms_device,
                 MetaKmsPlane  *kms_plane)
{
  MetaKmsPlaneType kms_plane_type = meta_kms_plane_get_plane_type (kms_plane);
  GList *l;

  for (l = meta_kms_device_get_crtcs (kms_device); l; l = l->next)
    {
      MetaKmsCrtc *kms_crtc = l->data;
      MetaCrtcKms *crtc_kms = meta_crtc_kms_from_kms_crtc (kms_crtc);

      if (crtc_assigned_plane (crtc_kms, kms_plane_type) == kms_plane)
        {
          meta_topic (META_DEBUG_KMS,
                      "Plane %u is currently active as %s for CRTC %u\n",
                      meta_kms_plane_get_id (kms_plane),
                      meta_kms_plane_type_to_string(kms_plane_type),
                      meta_kms_crtc_get_id (kms_crtc));
          return TRUE;
        }
    }

  return FALSE;
}

static MetaKmsPlane *
find_unassigned_plane (MetaCrtcKms      *crtc_kms,
                       MetaKmsPlaneType  kms_plane_type,
                       GPtrArray        *crtc_assignments)
{
  MetaKmsCrtc *kms_crtc = meta_crtc_kms_get_kms_crtc (crtc_kms);
  MetaKmsDevice *kms_device = meta_kms_crtc_get_device (kms_crtc);
  MetaKmsPlane *assigned_plane = crtc_assigned_plane (crtc_kms, kms_plane_type);
  GList *l;

  if (assigned_plane)
    {
      meta_topic (META_DEBUG_KMS, "Reusing assigned %s plane %u for CRTC %u\n",
                  meta_kms_plane_type_to_string (kms_plane_type),
                  meta_kms_plane_get_id (assigned_plane),
                  meta_kms_crtc_get_id (kms_crtc));
      return assigned_plane;
    }

  for (l = meta_kms_device_get_planes (kms_device); l; l = l->next)
    {
      MetaKmsPlane *kms_plane = l->data;

      if (meta_kms_plane_get_plane_type (kms_plane) != kms_plane_type)
        continue;

      if (!meta_kms_plane_is_usable_with (kms_plane, kms_crtc))
        continue;

      if (is_plane_assigned (kms_plane, kms_plane_type,
                             crtc_assignments))
        continue;

      if (is_plane_active (kms_device, kms_plane))
        continue;

      meta_topic (META_DEBUG_KMS,
                  "Plane %u is unassigned and can be used as %s for CRTC %u\n",
                  meta_kms_plane_get_id (kms_plane),
                  meta_kms_plane_type_to_string (kms_plane_type),
                  meta_kms_crtc_get_id (kms_crtc));

      return kms_plane;
    }

  return NULL;
}
